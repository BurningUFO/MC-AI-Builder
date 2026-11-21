package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.world.entity.Entity;

import java.util.List;
import java.util.UUID;

interface ConstructionWorker {
    record Result(String status, String reason, JsonObject evidence) {}

    void prepare(List<BuildPlan.Target> targets, boolean removals);

    boolean attach(UUID id);

    UUID id();

    boolean isActor(Entity entity);

    // Returns null while the worker walks or mines. The callback durably records the next mutation.
    Result act(WorkStep step, Runnable beforeInteraction);

    JsonObject status();

    void halt();

    void close();
}
