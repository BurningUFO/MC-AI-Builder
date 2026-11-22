package dev.mcaibuilder.bridge;

import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.inventory.CraftingContainer;
import net.minecraft.world.inventory.TransientCraftingContainer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.AbstractCookingRecipe;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.item.crafting.RecipeManager;
import net.minecraftforge.common.ForgeHooks;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class CraftingAssistant {
    enum Station {
        HAND, TABLE, FURNACE;

        net.minecraft.world.level.block.Block block() {
            return switch (this) {
                case TABLE -> net.minecraft.world.level.block.Blocks.CRAFTING_TABLE;
                case FURNACE -> net.minecraft.world.level.block.Blocks.FURNACE;
                case HAND -> net.minecraft.world.level.block.Blocks.AIR;
            };
        }
    }

    record Formula(Recipe<?> recipe, Item output, int count, Station station,
                   Map<Item, Integer> ingredients, List<Item> pattern, int width, int height, int cookTicks) {
        ItemStack assemble(ServerLevel level) {
            if (recipe instanceof CraftingRecipe crafting) {
                var grid = grid(pattern, width, height);
                return crafting.matches(grid, level) ? crafting.assemble(grid, level.registryAccess()) : ItemStack.EMPTY;
            }
            if (recipe instanceof AbstractCookingRecipe cooking) {
                var input = new SimpleContainer(new ItemStack(pattern.get(0)));
                return cooking.matches(input, level) ? cooking.assemble(input, level.registryAccess()) : ItemStack.EMPTY;
            }
            return ItemStack.EMPTY;
        }
    }

    record Batch(Formula formula, int times, Map<Item, Integer> inputs, int heatBefore, int heatAfter) {
        ItemStack output(ServerLevel level) {
            ItemStack result = formula.assemble(level);
            if (!result.is(formula.output) || result.getCount() != formula.count) {
                return ItemStack.EMPTY;
            }
            return result.copyWithCount(result.getCount() * times);
        }
    }

    record Plan(Map<Item, Integer> fetch, List<Batch> batches) {}
    record Estimate(Map<Item, Integer> missing, Map<Item, Integer> crafted, Map<Item, Integer> fetched) {}
    private static final List<String> WOODS = List.of("oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
            "mangrove", "cherry", "bamboo", "crimson", "warped");

    private final ServerLevel level;
    private final RecipeManager manager;
    private final Map<Item, List<Formula>> recipes = new LinkedHashMap<>();
    private final List<Item> fuel = new ArrayList<>();

    CraftingAssistant(ServerLevel level) {
        this.level = level;
        manager = level.getRecipeManager();
        fuel.addAll(List.of(Items.COAL, Items.CHARCOAL));
        for (String wood : WOODS) {
            Item planks = item(wood + "_planks");
            if (wood.equals("bamboo")) {
                craft("bamboo_planks", planks, Station.HAND, 1, item("bamboo_block"));
                craft("bamboo_planks", planks, Station.HAND, 1, item("stripped_bamboo_block"));
            } else {
                boolean nether = wood.equals("crimson") || wood.equals("warped");
                for (String ending : nether ? List.of("stem", "hyphae") : List.of("log", "wood")) {
                    for (String prefix : List.of("", "stripped_")) {
                        Item raw = item(prefix + wood + "_" + ending);
                        craft(wood + "_planks", planks, Station.HAND, 1, raw);
                        if (!nether) {
                            fuel.add(raw);
                            smelt("charcoal", Items.CHARCOAL, raw);
                        }
                    }
                }
            }
            fuel.add(planks);
            shapes(wood, planks);
            craft("stick", Items.STICK, Station.HAND, 1, planks, planks);
            craft("crafting_table", Items.CRAFTING_TABLE, Station.HAND, 2, planks, planks, planks, planks);
        }
        fuel.add(Items.STICK);
        craft("torch", Items.TORCH, Station.HAND, 1, Items.COAL, Items.STICK);
        craft("torch", Items.TORCH, Station.HAND, 1, Items.CHARCOAL, Items.STICK);
        smelt("stone", Items.STONE, Items.COBBLESTONE);
        smelt("glass", Items.GLASS, Items.SAND);
        craft("stone_bricks", Items.STONE_BRICKS, Station.HAND, 2, Items.STONE, Items.STONE, Items.STONE, Items.STONE);
        shapes("brick", Items.BRICKS);
        shapes("stone", Items.STONE);
        shapes("stone_brick", Items.STONE_BRICKS);
        shapes("cobblestone", Items.COBBLESTONE);
        craft("furnace", Items.FURNACE, Station.TABLE, 3, Items.COBBLESTONE, Items.COBBLESTONE, Items.COBBLESTONE,
                Items.COBBLESTONE, Items.AIR, Items.COBBLESTONE, Items.COBBLESTONE, Items.COBBLESTONE, Items.COBBLESTONE);
        craft("iron_nugget", Items.IRON_NUGGET, Station.HAND, 1, Items.IRON_INGOT);
        craft("lantern", Items.LANTERN, Station.TABLE, 3, Items.IRON_NUGGET, Items.IRON_NUGGET, Items.IRON_NUGGET,
                Items.IRON_NUGGET, Items.TORCH, Items.IRON_NUGGET, Items.IRON_NUGGET, Items.IRON_NUGGET, Items.IRON_NUGGET);
        tools("stone", Items.COBBLESTONE);
        tools("iron", Items.IRON_INGOT);
    }

    private void shapes(String material, Item base) {
        craft(material + "_stairs", item(material + "_stairs"), Station.TABLE, 3,
                base, Items.AIR, Items.AIR, base, base, Items.AIR, base, base, base);
        craft(material + "_slab", item(material + "_slab"), Station.TABLE, 3, base, base, base);
    }

    private void tools(String tier, Item head) {
        craft(tier + "_pickaxe", item(tier + "_pickaxe"), Station.TABLE, 3,
                head, head, head, Items.AIR, Items.STICK, Items.AIR, Items.AIR, Items.STICK, Items.AIR);
        craft(tier + "_axe", item(tier + "_axe"), Station.TABLE, 2,
                head, head, head, Items.STICK, Items.AIR, Items.STICK);
        craft(tier + "_shovel", item(tier + "_shovel"), Station.TABLE, 1, head, Items.STICK, Items.STICK);
    }

    private static Item item(String path) {
        Item item = ForgeRegistries.ITEMS.getValue(ResourceLocation.tryParse("minecraft:" + path));
        return item == null ? Items.AIR : item;
    }

    private static CraftingContainer grid(List<Item> pattern, int width, int height) {
        var contents = net.minecraft.core.NonNullList.withSize(width * height, ItemStack.EMPTY);
        for (int slot = 0; slot < pattern.size(); slot++) {
            Item item = pattern.get(slot);
            if (item != Items.AIR) {
                contents.set(slot, new ItemStack(item));
            }
        }
        return new TransientCraftingContainer(null, width, height, contents);
    }

    private void craft(String id, Item output, Station station, int rowWidth, Item... pattern) {
        int size = station == Station.HAND ? 2 : 3;
        var cells = new ArrayList<Item>();
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                int index = y * rowWidth + x;
                cells.add(x < rowWidth && index < pattern.length ? pattern[index] : Items.AIR);
            }
        }
        recipe(id, output, station, cells, size, size);
    }

    private void smelt(String id, Item output, Item input) {
        recipe(id, output, Station.FURNACE, List.of(input), 1, 1);
    }

    private void recipe(String id, Item output, Station station, List<Item> pattern, int width, int height) {
        var recipe = manager.byKey(ResourceLocation.tryParse("minecraft:" + id)).orElse(null);
        if (recipe == null || output == Items.AIR || !recipe.getResultItem(level.registryAccess()).is(output)) {
            return;
        }
        var inputs = new LinkedHashMap<Item, Integer>();
        pattern.stream().filter(item -> item != Items.AIR).forEach(item -> inputs.merge(item, 1, Integer::sum));
        int cooking = recipe instanceof AbstractCookingRecipe furnace ? furnace.getCookingTime() : 0;
        int count = recipe.getResultItem(level.registryAccess()).getCount();
        var formula = new Formula(recipe, output, count, station, inputs, pattern, width, height, cooking);
        if (formula.assemble(level).is(output)) {
            recipes.computeIfAbsent(output, ignored -> new ArrayList<>()).add(formula);
        }
    }

    boolean current() {
        return level.getRecipeManager() == manager;
    }

    Estimate estimate(Map<Item, Integer> needs, Map<Item, Item> substitutes, Map<Item, Integer> cargo,
                      Map<Item, Integer> stock, Set<Station> stations, int heat) {
        var budget = new Budget(cargo, stock, stations, heat);
        var shortages = reserve(needs, substitutes, budget);
        var direct = Map.copyOf(budget.fetch);
        var missing = new LinkedHashMap<Item, Integer>();
        var crafted = new LinkedHashMap<Item, Integer>();
        shortages.forEach((item, amount) -> {
            int made = produce(item, substitutes.get(item), amount, budget);
            if (made < amount) {
                missing.put(item, amount - made);
            }
            if (made > 0) {
                crafted.put(item, made);
            }
        });
        var fetched = new LinkedHashMap<Item, Integer>();
        budget.fetch.forEach((item, count) -> {
            int additional = count - direct.getOrDefault(item, 0);
            if (additional > 0) {
                fetched.put(item, additional);
            }
        });
        return new Estimate(missing, crafted, fetched);
    }

    Plan next(Item desired, Map<Item, Integer> needs, Map<Item, Item> substitutes, Map<Item, Integer> cargo,
              Map<Item, Integer> stock, Set<Station> stations, int heat) {
        var budget = new Budget(cargo, stock, stations, heat);
        var shortages = reserve(needs, substitutes, budget);
        // The reserved finished goods are left in chests until their own construction trip.
        budget.fetch.clear();
        int capacity = switch (Warehouse.id(desired)) {
            case "minecraft:stone", "minecraft:stone_bricks", "minecraft:stone_stairs", "minecraft:stone_slab",
                    "minecraft:stone_brick_stairs", "minecraft:stone_brick_slab", "minecraft:glass" -> 8;
            default -> desired.getMaxStackSize();
        };
        int needed = Math.min(capacity, shortages.getOrDefault(desired, 1));
        return produce(desired, substitutes.get(desired), needed, budget) == 0 ? null
                : new Plan(budget.fetch, budget.batches);
    }

    private static Map<Item, Integer> reserve(Map<Item, Integer> needs, Map<Item, Item> substitutes, Budget budget) {
        var shortages = new LinkedHashMap<Item, Integer>();
        // Give every exact match first claim before considering interchangeable tools.
        needs.forEach((item, count) -> shortages.put(item, count - budget.take(item, count)));
        shortages.replaceAll((item, count) -> {
            Item substitute = substitutes.get(item);
            return substitute == null ? count : count - budget.take(substitute, count);
        });
        shortages.values().removeIf(count -> count == 0);
        return shortages;
    }

    private int produce(Item target, Item substitute, int amount, Budget budget) {
        int made = 0;
        if (substitute != null) {
            made = fabricate(substitute, amount, budget);
        }
        return made + fabricate(target, amount - made, budget);
    }

    private int fabricate(Item item, int needed, Budget budget) {
        if (needed <= 0) {
            return 0;
        }
        int remaining = needed;
        for (Formula formula : recipes.getOrDefault(item, List.of())) {
            if (remaining == 0) {
                break;
            }
            int produced = Warehouse.tool(item) ? new ItemStack(item).getMaxDamage() : formula.count;
            int max = Math.min((remaining + produced - 1) / produced, Math.max(1, item.getMaxStackSize() / formula.count));
            while (max > 0 && remaining > 0) {
                var test = new Budget(budget);
                if (!schedule(formula, max, test)) {
                    max /= 2;
                    continue;
                }
                int increase = max * produced;
                int used = Math.min(increase, remaining);
                remaining -= used;
                test.cargo.merge(item, increase - used, Integer::sum);
                budget.copyFrom(test);
                max = Math.min(max, (remaining + produced - 1) / produced);
            }
        }
        return needed - remaining;
    }

    private boolean schedule(Formula formula, int times, Budget budget) {
        if (!station(formula.station, budget)) {
            return false;
        }
        int neededHeat = formula.cookTicks * times;
        var inputs = new LinkedHashMap<Item, Integer>();
        for (var entry : formula.ingredients.entrySet()) {
            Item item = entry.getKey();
            int count = times * entry.getValue();
            if (!require(item, count, budget)) {
                return false;
            }
            inputs.put(item, count);
        }
        int heatBefore = budget.heat;
        if (neededHeat > 0) {
            for (Item kind : fuel) {
                if (budget.heat >= neededHeat) {
                    break;
                }
                int burns = ForgeHooks.getBurnTime(new ItemStack(kind), net.minecraft.world.item.crafting.RecipeType.SMELTING);
                if (burns <= 0) {
                    continue;
                }
                int fuelCount = budget.take(kind, (neededHeat - budget.heat + burns - 1) / burns);
                inputs.merge(kind, fuelCount, Integer::sum);
                budget.heat += fuelCount * burns;
            }
            if (budget.heat < neededHeat) {
                return false;
            }
            budget.heat -= neededHeat;
        }
        inputs.values().removeIf(value -> value == 0);
        budget.batches.add(new Batch(formula, times, inputs, heatBefore, budget.heat));
        return true;
    }

    private boolean station(Station station, Budget budget) {
        if (station == Station.HAND || budget.stations.contains(station)) {
            return true;
        }
        Item item = station == Station.TABLE ? Items.CRAFTING_TABLE : Items.FURNACE;
        if (!require(item, 1, budget)) {
            return false;
        }
        budget.stations.add(station);
        // A builder-owned furnace must be recoverable after the last batch.
        return station != Station.FURNACE || budget.take(Items.IRON_PICKAXE, 1) > 0
                || require(Items.STONE_PICKAXE, 1, budget);
    }

    private boolean require(Item item, int count, Budget budget) {
        int remaining = count - budget.take(item, count);
        return fabricate(item, remaining, budget) == remaining;
    }

    private static final class Budget {
        Map<Item, Integer> cargo;
        Map<Item, Integer> stock;
        Map<Item, Integer> fetch;
        List<Batch> batches;
        java.util.EnumSet<Station> stations;
        int heat;

        Budget(Map<Item, Integer> cargo, Map<Item, Integer> stock, Set<Station> stations, int heat) {
            this.cargo = new LinkedHashMap<>(cargo);
            this.stock = new LinkedHashMap<>(stock);
            fetch = new LinkedHashMap<>();
            batches = new ArrayList<>();
            this.stations = java.util.EnumSet.of(Station.HAND);
            this.stations.addAll(stations);
            this.heat = heat;
        }

        Budget(Budget source) {
            this(source.cargo, source.stock, source.stations, source.heat);
            fetch = new LinkedHashMap<>(source.fetch);
            batches = new ArrayList<>(source.batches);
        }

        void copyFrom(Budget source) {
            cargo = source.cargo;
            stock = source.stock;
            fetch = source.fetch;
            batches = source.batches;
            stations = source.stations;
            heat = source.heat;
        }

        int take(Item item, int count) {
            int held = Math.min(cargo.getOrDefault(item, 0), count);
            int loaded = Math.min(stock.getOrDefault(item, 0), count - held);
            cargo.put(item, cargo.getOrDefault(item, 0) - held);
            stock.put(item, stock.getOrDefault(item, 0) - loaded);
            if (loaded > 0) {
                fetch.merge(item, Warehouse.tool(item) ? 1 : loaded,
                        (prior, more) -> Warehouse.tool(item) ? Math.max(prior, more) : prior + more);
            }
            return held + loaded;
        }
    }
}
