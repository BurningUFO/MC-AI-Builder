package dev.mcaibuilder.bridge;

/** Run offline without opening a world. These cases contain native properties but no block IDs. */
public final class TerrainTraitsSmoke {
    private static void expect(String kind, TerrainTraits traits) {
        if (!kind.equals(traits.removalKind())) {
            throw new AssertionError("Expected " + kind + " for " + traits);
        }
    }

    public static void main(String[] args) {
        expect("vegetation", new TerrainTraits(true, false, true, 0, false, false, false, false, false));
        expect("vegetation", new TerrainTraits(false, true, true, .1f, false, false, false, false, false));
        expect("unknown", new TerrainTraits(true, true, true, 0, true, false, false, false, false));
        expect("unknown", new TerrainTraits(true, true, true, 0, false, true, false, false, false));
        expect("unknown", new TerrainTraits(false, false, true, 0, false, false, false, false, false));
        expect("unknown", new TerrainTraits(true, true, false, 1, false, false, false, false, false));
        expect("soil", new TerrainTraits(false, false, false, .5f, false, false, true, false, false));
        expect("soil", new TerrainTraits(false, false, false, .6f, false, false, false, true, false));
        expect("stone", new TerrainTraits(false, false, false, 1.5f, false, false, false, false, true));
        expect("unknown", new TerrainTraits(false, false, false, 1.5f, false, false, false, false, false));
        expect("unknown", new TerrainTraits(false, false, false, -1, false, false, true, false, true));
        System.out.println("11 component-based clearance capability cases passed.");
    }
}
