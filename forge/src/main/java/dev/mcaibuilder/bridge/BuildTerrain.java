package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.StairBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

import java.util.Set;
import java.util.function.Function;

/** A conservative set of natural supports for surveyed terrain. */
final class BuildTerrain {
    static final Set<Block> NATURAL_SUPPORTS = Set.of(Blocks.GRASS_BLOCK, Blocks.DIRT, Blocks.COARSE_DIRT,
            Blocks.PODZOL, Blocks.ROOTED_DIRT, Blocks.STONE, Blocks.GRANITE, Blocks.DIORITE, Blocks.ANDESITE,
            Blocks.DEEPSLATE, Blocks.COBBLED_DEEPSLATE, Blocks.MOSS_BLOCK, Blocks.TERRACOTTA, Blocks.BEDROCK);

    private BuildTerrain() {}

    static boolean coversGrass(ServerLevel level, BlockPos above, BlockState cover, BlockState below) {
        return below.is(Blocks.GRASS_BLOCK) && cover.isSolidRender(level, above);
    }

    static JsonArray coveredGrass(ServerLevel level, BuildSite site, BuildPlan plan,
                                  Function<BlockPos, BlockState> baseline) {
        JsonArray report = new JsonArray();
        for (BuildPlan.Target target : plan.targets()) {
            BlockPos floor = target.world().below();
            BlockState before = baseline.apply(floor);
            if (site.area().contains(floor) && plan.at(floor) == null
                    && coversGrass(level, target.world(), target.state(), before)) {
                JsonObject row = new JsonObject();
                row.add("world", Wire.position(floor));
                row.add("before", Wire.state(before));
                row.add("possible", Wire.state(Blocks.DIRT.defaultBlockState()));
                report.add(row);
            }
        }
        return report;
    }

    static boolean canStand(ServerLevel level, BlockPos feet) {
        return canStand(level, feet, level::getBlockState);
    }

    static boolean canStand(ServerLevel level, BlockPos feet, Function<BlockPos, BlockState> blocks) {
        if (!level.hasChunkAt(feet)) {
            return false;
        }
        BlockPos floor = feet.below();
        BlockPos head = feet.above();
        BlockState ground = blocks.apply(floor);
        BlockState lower = blocks.apply(feet);
        BlockState upper = blocks.apply(head);
        var floorShape = ground.getCollisionShape(level, floor);
        // A centered actor overlaps the upper tread of a native stair, even without a full top face.
        boolean tread = ground.getBlock() instanceof StairBlock && !floorShape.isEmpty()
                && floorShape.max(Direction.Axis.Y) == 1.0;
        return ground.getFluidState().isEmpty() && !WorldObservation.hazard(ground)
                && (ground.isFaceSturdy(level, floor, Direction.UP) || tread || !floorShape.isEmpty()
                && floorShape.max(Direction.Axis.Y) == 0.5)
                && lower.getFluidState().isEmpty() && lower.getCollisionShape(level, feet).isEmpty()
                && upper.getFluidState().isEmpty() && upper.getCollisionShape(level, head).isEmpty();
    }

    static Vec3 standPoint(ServerLevel level, BlockPos feet) {
        BlockPos floor = feet.below();
        var shape = level.getBlockState(floor).getCollisionShape(level, floor);
        return Vec3.atBottomCenterOf(feet).add(0, shape.isEmpty() ? 0 : shape.max(Direction.Axis.Y) - 1, 0);
    }
}
