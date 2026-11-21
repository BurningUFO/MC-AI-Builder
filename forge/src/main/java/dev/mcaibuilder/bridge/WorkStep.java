package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.Set;

record WorkStep(BuildPlan.Target target, BlockState before, boolean remove) {
    BlockPos pos() {
        return target.world();
    }

    BlockState after() {
        return remove ? Blocks.AIR.defaultBlockState() : target.state();
    }

    JsonObject json(String status) {
        JsonObject data = new JsonObject();
        data.addProperty("operation", remove ? "remove" : "place");
        data.add("local", Wire.position(target.local()));
        data.add("world", Wire.position(pos()));
        data.add("expected", Wire.state(after()));
        data.add("expected_before", Wire.state(before));
        if (!remove) {
            data.addProperty("item_id", ForgeRegistries.ITEMS.getKey(target.item()).toString());
        }
        data.addProperty("status", status);
        return data;
    }

    static WorkStep read(JsonObject data, BuildPlan plan, Set<Block> palette, BuildSite site) {
        BlockPos pos = Wire.position(data.get("world"), "step.world");
        BlockPos local = Wire.position(data.get("local"), "step.local");
        if (!site.canBuildAt(pos) || !plan.worldPosition(local).equals(pos)) {
            throw Wire.invalid("Journal step outside its approved placement");
        }
        String operation = Wire.string(data.get("operation"), "operation");
        BlockState before = Wire.readState(Wire.object(data.get("expected_before"), "expected_before"));
        BlockState after = Wire.readState(Wire.object(data.get("expected"), "expected"));
        return switch (operation) {
            case "remove" -> {
                if (!palette.contains(before.getBlock()) || after != Blocks.AIR.defaultBlockState()) {
                    throw Wire.invalid("Unsafe removal in the journal");
                }
                yield new WorkStep(new BuildPlan.Target(local, pos, before, before.getBlock().asItem()), before, true);
            }
            case "place" -> {
                BuildPlan.Target target = plan.at(pos);
                if (target == null || target.state() != after || (!before.isAir() && before != after)) {
                    throw Wire.invalid("Unsafe placement in the journal");
                }
                yield new WorkStep(target, before, false);
            }
            default -> throw Wire.invalid("Unknown journal operation");
        };
    }
}
