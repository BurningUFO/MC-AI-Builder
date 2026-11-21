package dev.mcaibuilder.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

record ArchitectConfig(String token, Set<UUID> players) {
    static ArchitectConfig read(JsonElement value, String adminToken) {
        if (value == null) {
            return null;
        }
        JsonObject data = Wire.object(value, "architect");
        String token = Wire.string(data.get("token"), "architect.token");
        var users = Wire.array(data.get("players"), "architect.players");
        if (!token.matches("[0-9a-f]{64}") || token.equals(adminToken) || users.isEmpty() || users.size() > 32) {
            throw Wire.invalid("Architect needs its own token and an explicit player UUID list");
        }
        Set<UUID> players = new HashSet<>();
        for (JsonElement user : users) {
            try {
                players.add(UUID.fromString(Wire.string(user, "architect player UUID")));
            } catch (IllegalArgumentException error) {
                throw Wire.invalid("Invalid architect player UUID");
            }
        }
        return new ArchitectConfig(token, Set.copyOf(players));
    }
}
