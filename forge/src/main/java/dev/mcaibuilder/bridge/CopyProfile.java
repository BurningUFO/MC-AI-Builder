package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraftforge.common.util.FakePlayer;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.Set;
import java.util.UUID;

/** An explicitly launched, isolated singleplayer copy and its saved local identity. */
record CopyProfile(UUID id, UUID owner, String playerName, int port, String token,
                   ArchitectConfig architect, Path world) {
    static final String FOLDER = "bmc4-copy";
    static final String PROPERTY = "mcaibuilder.worldCopy";
    static final String MARKER = ".mcaibuilder-world-copy.json";

    static boolean requested() {
        return System.getProperty(PROPERTY) != null;
    }

    static boolean fixture(Path root) {
        return requested() && Boolean.getBoolean("mcaibuilder.copySmoke")
                && Files.isRegularFile(root.resolve(MARKER), LinkOption.NOFOLLOW_LINKS);
    }

    static CopyProfile read(Path gameDirectory) throws IOException {
        Path game = gameDirectory.toAbsolutePath().normalize();
        if (!game.toRealPath().equals(game) || game.getParent() == null
                || !game.getParent().getFileName().toString().equals("world-copies")) {
            throw new IOException("Expected the managed world-copies directory");
        }
        JsonObject marker = json(game.resolve(MARKER));
        JsonObject config = json(game.resolve("config").resolve("mcaibuilder-copy.json"));
        UUID id = BuildPlan.id(marker, "copy_id");
        UUID owner = BuildPlan.id(marker, "player_uuid");
        String playerName = Wire.string(marker.get("player_name"), "player_name");
        int port = Wire.integer(config.get("port"), "port");
        String token = Wire.string(config.get("token"), "token");
        String scoped = Wire.string(config.get("architect_token"), "architect_token");
        Path world = game.resolve("saves").resolve(FOLDER);
        if (Wire.integer(marker.get("schema"), "schema") != 1
                || !id.toString().equals(System.getProperty(PROPERTY))
                || !id.toString().replace("-", "").equals(game.getFileName().toString())
                || !id.equals(BuildPlan.id(config, "copy_id")) || !owner.equals(BuildPlan.id(config, "player_uuid"))
                || !playerName.matches("[A-Za-z0-9_]{1,16}") || port < 1024 || port > 65535
                || !token.matches("[a-f0-9]{64}") || !scoped.matches("[a-f0-9]{64}") || token.equals(scoped)
                || !world.equals(world.toRealPath()) || !Files.isRegularFile(world.resolve("level.dat"), LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("The isolated world marker, owner or Bridge configuration does not match");
        }
        return new CopyProfile(id, owner, playerName, port, token, new ArchitectConfig(scoped, Set.of(owner)), world);
    }

    boolean matches(MinecraftServer game) throws IOException {
        return !game.isDedicatedServer() && game.getWorldPath(LevelResource.ROOT).toRealPath().equals(world);
    }

    boolean owns(MinecraftServer game, ServerPlayer player) {
        return player != null && !(player instanceof FakePlayer) && owner.equals(player.getUUID())
                && playerName.equals(player.getGameProfile().getName()) && game.isSingleplayerOwner(player.getGameProfile());
    }

    private static JsonObject json(Path path) throws IOException {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || !path.equals(path.toRealPath())) {
            throw new IOException("Expected a local copy configuration file");
        }
        return Wire.object(JsonParser.parseString(Files.readString(path)), "world copy");
    }
}
