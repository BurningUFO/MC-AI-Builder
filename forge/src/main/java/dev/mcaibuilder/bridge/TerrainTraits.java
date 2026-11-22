package dev.mcaibuilder.bridge;

/** Properties used to infer a removal skill. The live surroundings are checked separately. */
record TerrainTraits(boolean plant, boolean replaceable, boolean collisionFree, float hardness,
                     boolean ownedFunction, boolean liquid, boolean soil, boolean loose, boolean nativeStone) {
    String removalKind() {
        if (ownedFunction || liquid || !Float.isFinite(hardness) || hardness < 0) {
            return "unknown";
        }
        if (collisionFree && (plant && hardness <= .8f || replaceable && hardness <= .25f)) {
            return "vegetation";
        }
        if (!collisionFree && (soil || loose) && hardness <= 2) {
            return "soil";
        }
        if (!collisionFree && nativeStone && hardness <= 5) {
            return "stone";
        }
        return "unknown";
    }
}
