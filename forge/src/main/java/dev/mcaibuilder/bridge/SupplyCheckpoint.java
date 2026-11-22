package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.UUID;

final class SupplyCheckpoint {
    final UUID owner;
    final JsonArray trips = new JsonArray();
    String phase = "ready";

    SupplyCheckpoint(UUID owner) {
        this.owner = owner;
    }

    JsonObject checkpoint() {
        JsonObject out = status(true);
        out.addProperty("owner", owner.toString());
        return out;
    }

    JsonObject status(boolean detailed) {
        JsonObject out = new JsonObject();
        out.addProperty("mode", "survival");
        out.addProperty("phase", phase);
        out.addProperty("trips", trips.size());
        if (detailed) {
            out.add("transfers", trips.deepCopy());
        }
        return out;
    }

    static SupplyCheckpoint read(JsonObject data, BuildSite site) {
        UUID owner = data.has("owner") ? BuildPlan.id(data, "owner")
                : Warehouse.Source.read(Wire.object(data.get("source"), "supply.source"), site).owner();
        SupplyCheckpoint state = new SupplyCheckpoint(owner);
        state.trips.addAll(Wire.array(data.get("transfers"), "supply.transfers"));
        return state;
    }
}
