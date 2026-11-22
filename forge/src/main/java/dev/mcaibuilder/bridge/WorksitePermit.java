package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Extended rights signed with the copy's existing owner and site approval. */
record WorksitePermit(String mode, long expiresAt, List<BuildSite.Box> protectedAreas) {
    static final Set<String> MODES = Set.of("manual", "auto", "terrain", "trees");
    static final WorksitePermit MANUAL = new WorksitePermit("manual", 0, List.of());

    WorksitePermit {
        if (!MODES.contains(mode) || protectedAreas.size() > 8
                || (mode.equals("manual") ? expiresAt != 0 : expiresAt <= 0)) {
            throw Wire.invalid("Invalid worksite authorization");
        }
        protectedAreas = List.copyOf(protectedAreas);
    }

    boolean active(long now) {
        return !mode.equals("manual") && expiresAt > now;
    }

    boolean protectedAt(BlockPos pos) {
        return protectedAreas.stream().anyMatch(area -> area.contains(pos));
    }

    JsonObject json() {
        var result = new JsonObject();
        result.addProperty("mode", mode);
        result.addProperty("expires_at_ms", expiresAt);
        var areas = new JsonArray();
        protectedAreas.forEach(area -> areas.add(area.json()));
        result.add("protected_areas", areas);
        return result;
    }

    static WorksitePermit read(JsonObject raw) {
        if (raw == null) {
            return MANUAL;
        }
        List<BuildSite.Box> areas = new ArrayList<>();
        for (var row : Wire.array(raw.get("protected_areas"), "protected areas")) {
            areas.add(BuildSite.Box.read(row, "protected area"));
        }
        var expiry = raw.get("expires_at_ms");
        if (expiry == null || !expiry.isJsonPrimitive() || !expiry.getAsJsonPrimitive().isNumber()) {
            throw Wire.invalid("Invalid worksite expiry");
        }
        return new WorksitePermit(Wire.string(raw.get("mode"), "worksite mode"), expiry.getAsLong(), areas);
    }
}
