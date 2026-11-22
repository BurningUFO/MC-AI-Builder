package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.entity.BarrelBlockEntity;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraftforge.common.capabilities.ForgeCapabilities;
import net.minecraftforge.items.IItemHandler;
import net.minecraftforge.items.ItemHandlerHelper;
import net.minecraftforge.items.wrapper.InvWrapper;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class Warehouse {
    private static final int RADIUS = 16;
    private static final int TRIP_ITEMS = 48;
    private static final Set<Item> TOOLS = Set.of(Items.IRON_AXE, Items.IRON_PICKAXE, Items.IRON_SHOVEL, Items.SHEARS,
            Items.STONE_AXE, Items.STONE_PICKAXE, Items.STONE_SHOVEL);

    private Warehouse() {}

    record Source(UUID owner, BlockPos pos) {
        JsonObject json() {
            JsonObject out = new JsonObject();
            out.addProperty("owner", owner.toString());
            out.add("position", Wire.position(pos));
            return out;
        }

        static Source read(JsonObject value, BuildSite site) {
            UUID owner = BuildPlan.id(value, "owner");
            BlockPos pos = Wire.position(value.get("position"), "warehouse.position");
            if (site.architect() == null || !site.architect().players().contains(owner)) {
                throw Wire.invalid("Supply area belongs to an unauthorized player");
            }
            return new Source(owner, pos);
        }
    }

    record Stock(BlockPos pos, IItemHandler items) {
        boolean has(Item item) {
            for (int slot = 0; slot < items.getSlots(); slot++) {
                if (items.getStackInSlot(slot).is(item) && usable(items.extractItem(slot, 1, true)) > 0) {
                    return true;
                }
            }
            return false;
        }
    }

    static List<Stock> scan(ServerLevel level, BuildSite site, Source source) {
        ServerPlayer owner = source == null ? null : level.getServer().getPlayerList().getPlayer(source.owner());
        if (owner == null || owner.serverLevel() != level) {
            return List.of();
        }
        BlockPos center = source.pos();
        int view = level.getServer().getPlayerList().getViewDistance() + 2;
        int playerX = owner.blockPosition().getX() >> 4;
        int playerZ = owner.blockPosition().getZ() >> 4;
        int minX = Math.max(playerX - view, Math.min(center.getX() - RADIUS, site.area().min().getX()) >> 4);
        int maxX = Math.min(playerX + view, Math.max(center.getX() + RADIUS, site.area().max().getX()) >> 4);
        int minZ = Math.max(playerZ - view, Math.min(center.getZ() - RADIUS, site.area().min().getZ()) >> 4);
        int maxZ = Math.min(playerZ + view, Math.max(center.getZ() + RADIUS, site.area().max().getZ()) >> 4);
        List<Stock> stocks = new ArrayList<>();
        for (int x = minX; x <= maxX; x++) {
            for (int z = minZ; z <= maxZ; z++) {
                var chunk = level.getChunkSource().getChunkNow(x, z);
                if (chunk == null) {
                    continue;
                }
                for (BlockEntity block : chunk.getBlockEntities().values()) {
                    BlockPos pos = block.getBlockPos();
                    if ((!site.area().contains(pos) && center.distSqr(pos) > RADIUS * RADIUS)
                            || site.worksite().protectedAt(pos) || !level.mayInteract(owner, pos)
                            || !level.getWorldBorder().isWithinBounds(pos)) {
                        continue;
                    }
                    Stock stock = open(block);
                    if (stock != null) {
                        stocks.add(stock);
                    }
                }
            }
        }
        stocks.sort(Comparator.comparingDouble(stock -> center.distSqr(stock.pos())));
        return stocks;
    }

    private static Stock open(BlockEntity block) {
        if (block.isRemoved()) {
            return null;
        }
        // Access each vanilla chest half once. The Forge chest capability can expose both halves.
        if (block instanceof ChestBlockEntity || block instanceof BarrelBlockEntity) {
            if (block.saveWithoutMetadata().contains("LootTable")) {
                return null;
            }
            return new Stock(block.getBlockPos(), new InvWrapper((Container) block));
        }
        String name = ForgeRegistries.BLOCKS.getKey(block.getBlockState().getBlock()).getPath();
        if (!(name.contains("barrel") || name.contains("chest") || name.contains("drawer") || name.endsWith("crate"))
                || name.contains("ender") || block.saveWithoutMetadata().contains("LootTable")) {
            return null;
        }
        return block.getCapability(ForgeCapabilities.ITEM_HANDLER, null)
                .map(items -> new Stock(block.getBlockPos(), items)).orElse(null);
    }

    static Item item(WorkStep step) {
        if (step.clearance()) {
            return switch (Clearance.kind(step.before())) {
                case "vegetation", "hand", "leaves", "soil" -> Items.AIR;
                case "logs", "wood" -> Items.IRON_AXE;
                default -> Items.IRON_PICKAXE;
            };
        }
        return step.remove() ? FakePlayersWorker.toolFor(step.before()) : step.target().item();
    }

    static boolean tool(Item item) {
        return TOOLS.contains(item);
    }

    static Item substitute(WorkStep step) {
        Item item = item(step);
        Item candidate = item == Items.IRON_PICKAXE ? Items.STONE_PICKAXE
                : item == Items.IRON_AXE ? Items.STONE_AXE
                : item == Items.IRON_SHOVEL ? Items.STONE_SHOVEL : null;
        return candidate != null && (!step.before().requiresCorrectToolForDrops()
                || new ItemStack(candidate).isCorrectToolForDrops(step.before())) ? candidate : null;
    }

    static Map<Item, Item> substitutes(List<WorkStep> steps, int cursor) {
        Map<Item, Item> values = new LinkedHashMap<>();
        Set<Item> incompatible = new java.util.HashSet<>();
        for (WorkStep step : steps.subList(cursor, steps.size())) {
            if (!step.remove() || optionalTool(step)) {
                continue;
            }
            Item alternative = substitute(step);
            if (alternative == null) {
                incompatible.add(item(step));
            } else {
                values.put(item(step), alternative);
            }
        }
        incompatible.forEach(values::remove);
        return values;
    }

    static boolean optionalTool(WorkStep step) {
        return step.remove() && !step.before().requiresCorrectToolForDrops();
    }

    static int usable(ItemStack stack) {
        if (stack.isEmpty()) {
            return 0;
        }
        if (TOOLS.contains(stack.getItem())) {
            return stack.getMaxDamage() - stack.getDamageValue();
        }
        return stack.hasTag() || stack.isDamageableItem() ? 0 : stack.getCount();
    }

    static String id(Item item) {
        return ForgeRegistries.ITEMS.getKey(item).toString();
    }

    static Map<Item, Integer> needs(List<WorkStep> steps, int cursor) {
        Map<Item, Integer> required = new LinkedHashMap<>();
        for (int index = cursor; index < steps.size(); index++) {
            WorkStep step = steps.get(index);
            Item item = item(step);
            if (step.before() != step.after() && item != Items.AIR && !optionalTool(step)) {
                required.merge(item, 1, Integer::sum);
            }
        }
        return required;
    }

    static Map<Item, Integer> carried(Container inventory) {
        Map<Item, Integer> totals = new LinkedHashMap<>();
        if (inventory != null) {
            for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
                add(totals, inventory.getItem(slot));
            }
        }
        return totals;
    }

    private static void add(Map<Item, Integer> totals, ItemStack stack) {
        int count = usable(stack);
        if (count > 0) {
            totals.merge(stack.getItem(), count, (a, b) -> (int) Math.min(Integer.MAX_VALUE, (long) a + b));
        }
    }

    static JsonObject items(Container inventory) {
        Map<Item, Integer> counts = new LinkedHashMap<>();
        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            ItemStack stack = inventory.getItem(slot);
            if (!stack.isEmpty()) {
                counts.merge(stack.getItem(), stack.getCount(), Integer::sum);
            }
        }
        return jsonTotals(counts);
    }

    static JsonObject jsonTotals(Map<Item, Integer> totals) {
        JsonObject json = new JsonObject();
        totals.forEach((item, count) -> json.addProperty(id(item), count));
        return json;
    }

    static Map<Item, Integer> stocked(List<Stock> stocks) {
        Map<Item, Integer> stocked = new LinkedHashMap<>();
        for (Stock stock : stocks) {
            for (int slot = 0; slot < stock.items().getSlots(); slot++) {
                ItemStack visible = stock.items().getStackInSlot(slot);
                if (!visible.isEmpty() && !stock.items().extractItem(slot, 1, true).isEmpty()) {
                    add(stocked, stock.items().extractItem(slot, visible.getCount(), true));
                }
            }
        }
        return stocked;
    }

    static JsonObject summary(List<WorkStep> steps, int cursor, Container inventory, List<Stock> stocks,
                              CraftingAssistant recipes, Set<CraftingAssistant.Station> stations, int heat) {
        Map<Item, Integer> cargo = carried(inventory);
        Map<Item, Integer> stocked = stocked(stocks);
        var needs = needs(steps, cursor);
        var substitutes = substitutes(steps, cursor);
        var estimate = recipes.estimate(needs, substitutes, cargo, stocked, stations, heat);
        JsonObject rows = new JsonObject();
        needs.forEach((item, need) -> {
            int owned = cargo.getOrDefault(item, 0);
            int supplied = stocked.getOrDefault(item, 0);
            JsonObject row = new JsonObject();
            row.addProperty("unit", TOOLS.contains(item) ? "durability" : "items");
            row.addProperty("required", need);
            row.addProperty("npc", owned);
            row.addProperty("warehouse", supplied);
            if (substitutes.containsKey(item)) {
                Item substitute = substitutes.get(item);
                row.addProperty("equivalent", id(substitute));
                row.addProperty("alternate_owned", (long) cargo.getOrDefault(substitute, 0) + stocked.getOrDefault(substitute, 0));
            }
            row.addProperty("craftable", estimate.crafted().getOrDefault(item, 0));
            row.addProperty("missing", estimate.missing().getOrDefault(item, 0));
            rows.add(id(item), row);
        });
        JsonObject result = new JsonObject();
        result.add("requirements", rows);
        result.add("recipe_inputs", jsonTotals(estimate.fetched()));
        result.add("warehouse_items", jsonTotals(stocked));
        result.add("npc_items", jsonTotals(cargo));
        result.addProperty("containers", stocks.size());
        return result;
    }

    static boolean missing(JsonObject summary) {
        return summary.getAsJsonObject("requirements").entrySet().stream()
                .anyMatch(entry -> entry.getValue().getAsJsonObject().get("missing").getAsInt() > 0);
    }

    static String shortages(JsonObject summary) {
        List<String> missing = new ArrayList<>();
        summary.getAsJsonObject("requirements").entrySet().forEach(entry -> {
            var row = entry.getValue().getAsJsonObject();
            if (row.get("missing").getAsInt() > 0) {
                missing.add(entry.getKey().replace("minecraft:", "") + " ×" + row.get("missing").getAsInt()
                        + (row.get("unit").getAsString().equals("durability") ? " 耐久" : ""));
            }
        });
        return missing.isEmpty() ? "附近材料够用。" : "还差 " + String.join("、", missing) + "。补到附近箱子后告诉我一声。";
    }

    static JsonObject transfer(Stock stock, SimpleContainer cargo, List<WorkStep> steps, int cursor) {
        return transfer(stock, cargo, needs(steps, cursor));
    }

    static JsonObject transfer(Stock stock, SimpleContainer cargo, Map<Item, Integer> wanted) {
        Map<Item, Integer> inventory = carried(cargo);
        Map<Item, Integer> moved = new LinkedHashMap<>();
        int remaining = TRIP_ITEMS;
        for (var entry : wanted.entrySet()) {
            Item item = entry.getKey();
            int needed = entry.getValue() - inventory.getOrDefault(item, 0);
            for (int slot = 0; slot < stock.items().getSlots() && needed > 0 && remaining > 0; slot++) {
                ItemStack present = stock.items().getStackInSlot(slot);
                if (!present.is(item) || usable(present) <= 0) {
                    continue;
                }
                ItemStack test = stock.items().extractItem(slot, TOOLS.contains(item) ? 1 : Math.min(needed, remaining), true);
                int count = Math.min(test.getCount(), room(cargo, test));
                if (count == 0) {
                    continue;
                }
                ItemStack taken = stock.items().extractItem(slot, count, false);
                if (taken.isEmpty()) {
                    continue;
                }
                int extracted = taken.getCount();
                ItemStack extra = cargo.addItem(taken);
                if (!extra.isEmpty()) {
                    ItemHandlerHelper.insertItemStacked(stock.items(), extra, false);
                }
                int added = extracted - extra.getCount();
                if (added > 0) {
                    moved.merge(item, added, Integer::sum);
                    needed -= TOOLS.contains(item) ? usable(taken) : added;
                    remaining -= added;
                }
            }
        }
        JsonObject result = new JsonObject();
        result.add("items", jsonTotals(moved));
        result.add("warehouse_position", Wire.position(stock.pos()));
        return result;
    }

    static int room(SimpleContainer cargo, ItemStack stack) {
        int capacity = 0;
        int limit = Math.min(cargo.getMaxStackSize(), stack.getMaxStackSize());
        for (int slot = 0; slot < cargo.getContainerSize(); slot++) {
            ItemStack present = cargo.getItem(slot);
            if (present.isEmpty()) {
                capacity += limit;
            } else if (ItemStack.isSameItemSameTags(present, stack)) {
                capacity += Math.max(0, limit - present.getCount());
            }
        }
        return capacity;
    }

    static boolean fabricate(SimpleContainer cargo, Map<Item, Integer> inputs, ItemStack result) {
        if (result.isEmpty()) {
            return false;
        }
        var remaining = new LinkedHashMap<>(inputs);
        var updated = new SimpleContainer(cargo.getContainerSize());
        for (int slot = 0; slot < cargo.getContainerSize(); slot++) {
            ItemStack stack = cargo.getItem(slot).copy();
            Item item = stack.getItem();
            int consume = stack.hasTag() ? 0 : Math.min(stack.getCount(), remaining.getOrDefault(item, 0));
            stack.shrink(consume);
            if (consume > 0) {
                remaining.put(item, remaining.get(item) - consume);
            }
            updated.setItem(slot, stack);
        }
        if (remaining.values().stream().anyMatch(amount -> amount != 0) || !updated.addItem(result).isEmpty()) {
            return false;
        }
        for (int slot = 0; slot < cargo.getContainerSize(); slot++) {
            cargo.setItem(slot, updated.getItem(slot));
        }
        return true;
    }

    static int find(Container inventory, Item item) {
        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            ItemStack stack = inventory.getItem(slot);
            if (stack.is(item) && usable(stack) > 0) {
                return slot;
            }
        }
        return -1;
    }
}
