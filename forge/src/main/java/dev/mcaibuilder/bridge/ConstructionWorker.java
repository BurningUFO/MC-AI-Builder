package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;

import java.util.List;

interface ConstructionWorker {
    record Result(String status, String reason, JsonObject evidence) {}

    void prepare(List<BuildPlan.Target> targets);

    // Returns null while the worker is walking to a suitable stance.
    Result act(BuildPlan.Target target);

    JsonObject status();

    void halt();

    void close();
}
