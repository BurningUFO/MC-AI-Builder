package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.level.block.state.BlockState;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

interface ConstructionWorker {
    record Result(String status, String reason, JsonObject evidence) {}

    void prepare(List<BuildPlan.Target> targets, boolean removals);

    boolean attach(UUID id);

    UUID id();

    boolean isActor(Entity entity);

    // Null means the NPC is still moving, mining or adjusting its stance.
    Result act(WorkStep step);

    void useScaffolds(Map<BlockPos, BlockState> blocks, Set<BlockPos> targets);

    boolean cleanScaffolds();

    void useSupplyArea(BlockPos center);

    boolean inspectStock(BlockPos pos);

    SimpleContainer inventory();

    boolean hasSupply(WorkStep step);

    CraftingAssistant recipes();

    Set<CraftingAssistant.Station> stations(BlockPos center);

    void useWorkshop(SupplyCheckpoint supply);

    boolean cleanWorkshop();

    boolean gatherScaffoldMaterial(int amount);

    void tickLife(boolean idle);

    void celebrate(BuildSite.Box bounds);

    void mentionSupply();

    JsonObject resupply(Supplier<List<Warehouse.Stock>> availableStock, List<WorkStep> steps, int cursor);

    JsonObject status();

    void profile(BuildPerformance performance);

    String activity();

    Vec3 position();

    void startSurvey();

    void moveForSurvey(BlockPos destination);

    boolean surveyArrived();

    void lookForSurvey(BlockPos point);

    void halt();

    void close();
}
