package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.minecraft.core.BlockPos;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

final class SupplyCheckpoint {
    final UUID owner;
    final JsonArray trips = new JsonArray();
    final JsonArray recipes = new JsonArray();
    final Map<BlockPos, CraftingAssistant.Station> temporary = new LinkedHashMap<>();
    int heat;
    BlockPos heatedFurnace;
    String phase = "ready";

    SupplyCheckpoint(UUID owner) {
        this.owner = owner;
    }

    JsonObject checkpoint() {
        JsonObject out = status(true);
        out.addProperty("owner", owner.toString());
        out.addProperty("furnace_heat", heat);
        if (heatedFurnace != null) {
            out.add("heated_furnace", Wire.position(heatedFurnace));
        }
        JsonArray workstations = new JsonArray();
        temporary.forEach((pos, station) -> {
            JsonObject entry = new JsonObject();
            entry.add("position", Wire.position(pos));
            entry.addProperty("station", station.name());
            workstations.add(entry);
        });
        out.add("temporary_workstations", workstations);
        return out;
    }

    JsonObject status(boolean detailed) {
        JsonObject out = new JsonObject();
        out.addProperty("mode", "survival");
        out.addProperty("phase", phase);
        out.addProperty("trips", trips.size());
        out.addProperty("recipe_batches", recipes.size());
        out.addProperty("workstations", temporary.size());
        if (detailed) {
            out.add("recipes", recipes.deepCopy());
            out.add("transfers", trips.deepCopy());
        }
        return out;
    }

    static SupplyCheckpoint read(JsonObject data, BuildSite site) {
        UUID owner = data.has("owner") ? BuildPlan.id(data, "owner")
                : Warehouse.Source.read(Wire.object(data.get("source"), "supply.source"), site).owner();
        SupplyCheckpoint state = new SupplyCheckpoint(owner);
        state.trips.addAll(Wire.array(data.get("transfers"), "supply.transfers"));
        if (data.has("recipes")) {
            state.recipes.addAll(Wire.array(data.get("recipes"), "supply.recipes"));
        }
        if (data.has("heated_furnace")) {
            state.heat = Wire.integer(data.get("furnace_heat"), "supply.furnace_heat");
            state.heatedFurnace = Wire.position(data.get("heated_furnace"), "supply.heated_furnace");
        }
        if (data.has("temporary_workstations")) {
            for (var element : Wire.array(data.get("temporary_workstations"), "supply.temporary_workstations")) {
                var entry = Wire.object(element, "workstation");
                var pos = Wire.position(entry.get("position"), "workstation.position");
                var name = Wire.string(entry.get("station"), "workstation.station");
                if (site.canBuildAt(pos) && ("TABLE".equals(name) || "FURNACE".equals(name))) {
                    state.temporary.put(pos, CraftingAssistant.Station.valueOf(name));
                }
            }
        }
        return state;
    }
}
