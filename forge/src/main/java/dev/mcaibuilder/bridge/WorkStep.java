package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.List;
import java.util.Set;

record WorkStep(BuildPlan.Target target, BlockState before, boolean remove, boolean clearance) {
    WorkStep(BuildPlan.Target target, BlockState before, boolean remove) {
        this(target, before, remove, false);
    }

    static WorkStep clear(BuildPlan plan, BlockPos pos, BlockState state) {
        BlockPos local = plan.localPosition(pos);
        return new WorkStep(new BuildPlan.Target(local, pos, state, state.getBlock().asItem()), state, true, true);
    }

    BlockPos secondary() {
        return clearance && Clearance.pairedPlant(before) ? pos().above() : null;
    }

    List<BlockPos> affected() {
        BlockPos secondary = secondary();
        return secondary == null ? List.of(pos()) : List.of(pos(), secondary);
    }

    BlockPos pos() {
        return target.world();
    }

    BlockState after() {
        return remove ? Blocks.AIR.defaultBlockState() : target.state();
    }

    JsonObject json(String status) {
        JsonObject data = new JsonObject();
        data.addProperty("operation", clearance ? "clear" : remove ? "remove" : "place");
        if (clearance) {
            data.addProperty("clearance_kind", Clearance.kind(before));
            if (secondary() != null) {
                data.add("also_clears", Wire.position(secondary()));
            }
        }
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
            case "clear" -> {
                if (!site.worldCopy() || site.worksite().mode().equals("manual") || before.isAir()
                        || before.hasBlockEntity() || !before.getFluidState().isEmpty()
                        || Clearance.kind(before).equals("unknown") || Clearance.upperHalf(before)
                        || Clearance.pairedPlant(before) && !site.canBuildAt(pos.above())
                        || after != Blocks.AIR.defaultBlockState()) {
                    throw Wire.invalid("Unsafe clearance in the journal");
                }
                yield clear(plan, pos, before);
            }
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
