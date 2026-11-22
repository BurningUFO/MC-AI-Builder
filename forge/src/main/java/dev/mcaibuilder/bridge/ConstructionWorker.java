package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.phys.Vec3;

import java.util.List;
import java.util.UUID;
import java.util.function.Consumer;

interface ConstructionWorker {
    record Result(String status, String reason, JsonObject evidence) {}

    void prepare(List<BuildPlan.Target> targets, boolean removals);

    boolean attach(UUID id);

    UUID id();

    boolean isActor(Entity entity);

    // Returns null while the worker walks or mines. The callback durably records the next mutation.
    Result act(WorkStep step, Runnable beforeInteraction);

    SimpleContainer inventory();

    boolean hasSupply(WorkStep step);

    JsonObject resupply(Warehouse.Source source, String stockHash, List<WorkStep> steps, int cursor,
                        boolean batchNearbyWork, Consumer<JsonObject> beforeTransfer);

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
