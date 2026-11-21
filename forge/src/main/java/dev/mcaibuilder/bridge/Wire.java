package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraftforge.registries.ForgeRegistries;

final class Wire {
    private Wire() {}

    static final class Problem extends RuntimeException {
        final int status;
        final String code;

        Problem(int status, String code, String message) {
            super(message);
            this.status = status;
            this.code = code;
        }
    }

    static Problem invalid(String message) {
        return new Problem(400, "invalid_request", message);
    }

    static Problem conflict(String code, String message) {
        return new Problem(409, code, message);
    }

    static JsonObject object(JsonElement value, String name) {
        if (value == null || !value.isJsonObject()) {
            throw invalid(name + " must be an object");
        }
        return value.getAsJsonObject();
    }

    static JsonArray array(JsonElement value, String name) {
        if (value == null || !value.isJsonArray()) {
            throw invalid(name + " must be an array");
        }
        return value.getAsJsonArray();
    }

    static String string(JsonElement value, String name) {
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
            throw invalid(name + " must be a string");
        }
        return value.getAsString();
    }

    static int integer(JsonElement value, String name) {
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()
                || !value.getAsString().matches("-?(0|[1-9][0-9]{0,9})")) {
            throw invalid(name + " must be a 32-bit integer");
        }
        try {
            return Integer.parseInt(value.getAsString());
        } catch (NumberFormatException e) {
            throw invalid(name + " is out of range");
        }
    }

    static BlockPos position(JsonElement value, String name) {
        JsonArray axes = array(value, name);
        if (axes.size() != 3) {
            throw invalid(name + " must have three axes");
        }
        int x = integer(axes.get(0), name);
        int y = integer(axes.get(1), name);
        int z = integer(axes.get(2), name);
        if (Math.abs((long) x) > 30_000_000L || Math.abs((long) y) > 30_000_000L
                || Math.abs((long) z) > 30_000_000L) {
            throw invalid(name + " is outside the Minecraft coordinate range");
        }
        return new BlockPos(x, y, z);
    }

    static JsonArray position(BlockPos pos) {
        JsonArray result = new JsonArray(3);
        result.add(pos.getX());
        result.add(pos.getY());
        result.add(pos.getZ());
        return result;
    }

    static JsonObject state(BlockState state) {
        JsonObject result = new JsonObject();
        result.addProperty("id", ForgeRegistries.BLOCKS.getKey(state.getBlock()).toString());
        JsonObject properties = new JsonObject();
        for (Property<?> property : state.getProperties()) {
            properties.addProperty(property.getName(), valueName(state, property));
        }
        result.add("properties", properties);
        return result;
    }

    private static <T extends Comparable<T>> String valueName(BlockState state, Property<T> property) {
        return property.getName(state.getValue(property));
    }
}
