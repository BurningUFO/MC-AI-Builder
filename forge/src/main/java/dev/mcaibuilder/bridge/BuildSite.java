package dev.mcaibuilder.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

record BuildSite(String world, int port, String token, Box area, BlockPos spawn) {
    static BuildSite read(Path config) throws IOException {
        if (!Files.isRegularFile(config)) {
            return null;
        }
        JsonObject data = Wire.object(JsonParser.parseString(Files.readString(config)), "config");
        String world = Wire.string(data.get("world"), "world");
        int port = Wire.integer(data.get("port"), "port");
        String token = Wire.string(data.get("token"), "token");
        Box area = Box.read(data.get("area"), "area");
        BlockPos spawn = Wire.position(data.get("npc_spawn"), "npc_spawn");
        if (world.isBlank() || port < 1024 || port > 65535 || !token.matches("[0-9a-f]{64}")
                || !area.contains(spawn) || !area.contains(spawn.above()) || spawn.getY() != area.min().getY()
                || area.halo().volume() > 24_000) {
            throw Wire.invalid("Invalid bridge world, port, token, or approved site");
        }
        return new BuildSite(world, port, token, area, spawn);
    }

    boolean canBuildAt(BlockPos pos) {
        return area.contains(pos) && !pos.equals(spawn) && !pos.equals(spawn.above());
    }

    void requireLoaded(ServerLevel level) {
        Box monitored = area.halo();
        if (!level.isInWorldBounds(monitored.min()) || !level.isInWorldBounds(monitored.max())
                || !level.getWorldBorder().isWithinBounds(monitored.min())
                || !level.getWorldBorder().isWithinBounds(monitored.max())) {
            throw Wire.conflict("site_out_of_world", "The approved site and its halo must fit in the world");
        }
        for (int x = monitored.min().getX() >> 4; x <= monitored.max().getX() >> 4; x++) {
            for (int z = monitored.min().getZ() >> 4; z <= monitored.max().getZ() >> 4; z++) {
                if (!level.getChunkSource().hasChunk(x, z)) {
                    throw Wire.conflict("site_not_loaded", "Keep the approved site loaded during construction");
                }
            }
        }
    }

    record Box(BlockPos min, BlockPos max) {
        Box {
            if (min.getX() > max.getX() || min.getY() > max.getY() || min.getZ() > max.getZ()) {
                throw Wire.invalid("Bounds must be ordered min to max");
            }
        }

        static Box read(JsonElement value, String name) {
            JsonObject box = Wire.object(value, name);
            return new Box(Wire.position(box.get("min"), name + ".min"), Wire.position(box.get("max"), name + ".max"));
        }

        boolean contains(BlockPos pos) {
            return contains(min, max, pos);
        }

        private static boolean contains(BlockPos min, BlockPos max, BlockPos pos) {
            return pos.getX() >= min.getX() && pos.getX() <= max.getX()
                    && pos.getY() >= min.getY() && pos.getY() <= max.getY()
                    && pos.getZ() >= min.getZ() && pos.getZ() <= max.getZ();
        }

        long volume() {
            long x = 1L + max.getX() - min.getX();
            long y = 1L + max.getY() - min.getY();
            long z = 1L + max.getZ() - min.getZ();
            return x > 24_000 || y > 24_000 || z > 24_000 ? Long.MAX_VALUE : x * y * z;
        }

        Box halo() {
            return new Box(min.offset(-1, -1, -1), max.offset(1, 1, 1));
        }

        JsonObject json() {
            JsonObject result = new JsonObject();
            result.add("min", Wire.position(min));
            result.add("max", Wire.position(max));
            return result;
        }
    }
}
