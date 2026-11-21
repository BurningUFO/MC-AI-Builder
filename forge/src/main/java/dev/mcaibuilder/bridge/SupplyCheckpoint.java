package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonArray;

final class SupplyCheckpoint {
    final Warehouse.Source source;
    final JsonArray trips = new JsonArray();
    String stockHash;
    JsonObject intent;
    String phase = "ready";
    boolean checked = true;

    SupplyCheckpoint(Warehouse.Source source, String stockHash) {
        this.source = source;
        this.stockHash = stockHash;
    }

    JsonObject checkpoint() {
        JsonObject out = status(true);
        out.add("source", source.json());
        out.add("intent", intent);
        return out;
    }

    JsonObject status(boolean detailed) {
        JsonObject out = new JsonObject();
        out.addProperty("mode", "survival");
        out.add("position", Wire.position(source.pos()));
        out.addProperty("warehouse_sha256", stockHash);
        out.addProperty("phase", phase);
        out.addProperty("in_flight", intent != null);
        out.addProperty("trips", trips.size());
        if (detailed) {
            out.add("transfers", trips.deepCopy());
        }
        return out;
    }

    static SupplyCheckpoint read(JsonObject data, BuildSite site) {
        String hash = Wire.string(data.get("warehouse_sha256"), "warehouse_sha256");
        if (!hash.matches("[0-9a-f]{64}")) {
            throw Wire.invalid("Invalid warehouse checkpoint digest");
        }
        SupplyCheckpoint state = new SupplyCheckpoint(
                Warehouse.Source.read(Wire.object(data.get("source"), "supply.source"), site), hash);
        state.trips.addAll(Wire.array(data.get("transfers"), "supply.transfers"));
        if (data.has("intent") && !data.get("intent").isJsonNull()) {
            state.intent = Wire.object(data.get("intent"), "supply.intent");
        }
        state.checked = false;
        return state;
    }
}
