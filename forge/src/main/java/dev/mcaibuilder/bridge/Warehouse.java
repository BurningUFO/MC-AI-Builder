package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.ChestType;
import net.minecraft.world.level.storage.loot.LootContext;
import net.minecraft.world.level.storage.loot.LootParams;
import net.minecraft.world.level.storage.loot.parameters.LootContextParamSets;
import net.minecraft.world.level.storage.loot.parameters.LootContextParams;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.items.wrapper.InvWrapper;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

final class Warehouse {
    private static final String SEAL = "mcaibuilder_supply";
    static final int TRIP_ITEMS = 16;
    static final int COTTAGE_TRIP_ITEMS = 48;
    private static final Set<Item> TOOLS = Set.of(Items.IRON_AXE, Items.IRON_PICKAXE, Items.IRON_SHOVEL, Items.SHEARS);

    private Warehouse() {}

    record Source(UUID owner, UUID id, BlockPos pos) {
        JsonObject json() {
            JsonObject out = new JsonObject();
            out.addProperty("owner", owner.toString());
            out.addProperty("source_id", id.toString());
            out.add("position", Wire.position(pos));
            return out;
        }

        static Source read(JsonObject value, BuildSite site) {
            BlockPos pos = Wire.position(value.get("position"), "warehouse.position");
            if (!site.canBuildAt(pos) || site.architect() == null
                    || !site.architect().players().contains(BuildPlan.id(value, "owner"))) {
                throw Wire.invalid("Warehouse approval is outside its site or player scope");
            }
            return new Source(BuildPlan.id(value, "owner"), BuildPlan.id(value, "source_id"), pos);
        }
    }

    static Source authorize(ServerLevel level, BuildSite site, ServerPlayer player, BlockPos pos) {
        if (!site.canBuildAt(pos) || !level.mayInteract(player, pos)) {
            throw Wire.conflict("warehouse_outside_site", "供料箱需位于批准区域内。");
        }
        Vec3 eye = player.getEyePosition();
        Vec3 center = Vec3.atCenterOf(pos);
        var hit = level.clip(new ClipContext(eye, center, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, player));
        if (eye.distanceToSqr(center) > 4.5 * 4.5 || hit.getType() != HitResult.Type.BLOCK
                || !hit.getBlockPos().equals(pos)) {
            throw Wire.conflict("warehouse_out_of_reach", "请走到可看见并触及供料箱的位置。");
        }
        ChestBlockEntity chest = singleChest(level, pos);
        Source source = new Source(player.getUUID(), UUID.randomUUID(), pos.immutable());
        chest.getPersistentData().putUUID(SEAL, source.id());
        chest.setChanged();
        return source;
    }

    static ChestBlockEntity open(ServerLevel level, Source source) {
        ChestBlockEntity chest = singleChest(level, source.pos());
        CompoundTag data = chest.getPersistentData();
        if (!data.hasUUID(SEAL) || !source.id().equals(data.getUUID(SEAL))) {
            throw Wire.conflict("warehouse_replaced", "获准的供料箱已更换。");
        }
        return chest;
    }

    private static ChestBlockEntity singleChest(ServerLevel level, BlockPos pos) {
        if (!level.hasChunkAt(pos)) {
            throw Wire.conflict("warehouse_not_loaded", "供料箱区块尚未加载。");
        }
        BlockState state = level.getBlockState(pos);
        if (!state.is(Blocks.CHEST) || state.getValue(BlockStateProperties.CHEST_TYPE) != ChestType.SINGLE
                || !(level.getBlockEntity(pos) instanceof ChestBlockEntity chest) || chest.saveWithoutMetadata().contains("LootTable")) {
            throw Wire.conflict("warehouse_unavailable", "供料需要普通的无战利品表原版单箱。");
        }
        return chest;
    }

    static Item item(WorkStep step) {
        if (step.clearance()) {
            return switch (Clearance.kind(step.before())) {
                case "vegetation", "hand" -> Items.AIR;
                case "leaves" -> Items.SHEARS;
                case "soil" -> Items.IRON_SHOVEL;
                case "logs", "wood" -> Items.IRON_AXE;
                default -> Items.IRON_PICKAXE;
            };
        }
        return step.remove() ? FakePlayersWorker.toolFor(step.before()) : step.target().item();
    }

    static int usable(ItemStack stack) {
        if (stack.isEmpty()) {
            return 0;
        }
        if (TOOLS.contains(stack.getItem())) {
            CompoundTag tag = stack.getTag();
            if (tag != null && !tag.getAllKeys().equals(Set.of("Damage"))) {
                return 0;
            }
            int damage = stack.getDamageValue();
            return damage >= 0 && damage < stack.getMaxDamage() ? stack.getMaxDamage() - damage : 0;
        }
        return stack.hasTag() || stack.isDamageableItem() ? 0 : stack.getCount();
    }

    static String digest(Container storage) {
        JsonArray states = new JsonArray();
        for (int slot = 0; slot < storage.getContainerSize(); slot++) {
            ItemStack stack = storage.getItem(slot);
            states.add(stack.isEmpty() ? "" : stack.save(new CompoundTag()).toString());
        }
        return Wire.sha256(states);
    }

    static String cargoDigest(Container inventory) {
        // Fake Players serializes its SimpleContainer as an item list. Slots may compact on load.
        Map<String, Integer> contents = new TreeMap<>();
        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            ItemStack stack = inventory.getItem(slot);
            if (!stack.isEmpty()) {
                CompoundTag key = stack.save(new CompoundTag());
                key.remove("Count");
                contents.merge(key.toString(), stack.getCount(), Integer::sum);
            }
        }
        JsonObject states = new JsonObject();
        contents.forEach(states::addProperty);
        return Wire.sha256(states);
    }

    private static SimpleContainer copy(Container storage) {
        SimpleContainer result = new SimpleContainer(storage.getContainerSize());
        for (int slot = 0; slot < storage.getContainerSize(); slot++) {
            result.setItem(slot, storage.getItem(slot).copy());
        }
        return result;
    }

    private static Map<Item, Integer> quantities(Container storage, boolean uses) {
        Map<Item, Integer> totals = new LinkedHashMap<>();
        if (storage != null) {
            for (int slot = 0; slot < storage.getContainerSize(); slot++) {
                ItemStack stack = storage.getItem(slot);
                if (!stack.isEmpty() && (!uses || usable(stack) > 0)) {
                    totals.merge(stack.getItem(), uses ? usable(stack) : stack.getCount(), Integer::sum);
                }
            }
        }
        return totals;
    }

    static JsonObject items(Container storage) {
        return jsonTotals(quantities(storage, false));
    }

    private static JsonObject jsonTotals(Map<Item, Integer> totals) {
        JsonObject result = new JsonObject();
        totals.forEach((item, count) -> result.addProperty(id(item), count));
        return result;
    }

    static String id(Item item) {
        return ForgeRegistries.ITEMS.getKey(item).toString();
    }

    static JsonObject summary(ServerLevel level, List<WorkStep> steps, int cursor, Container npc,
                              Container chest, JsonArray actions) {
        Map<Item, Integer> required = new LinkedHashMap<>();
        Map<Item, Integer> recoverable = new LinkedHashMap<>();
        for (int index = cursor; index < steps.size(); index++) {
            WorkStep step = steps.get(index);
            Item requirement = item(step);
            if (step.before() != step.after() && requirement != Items.AIR) {
                required.merge(requirement, 1, Integer::sum);
            }
            if (step.remove()) {
                estimateDrops(level, step, recoverable);
            }
        }
        Map<Item, Integer> carried = quantities(npc, true);
        Map<Item, Integer> stocked = quantities(chest, true);
        Map<Item, Integer> surplus = new LinkedHashMap<>(carried);
        JsonObject rows = new JsonObject();
        required.forEach((item, need) -> {
            int owned = carried.getOrDefault(item, 0);
            int supplied = stocked.getOrDefault(item, 0);
            int available = owned + supplied;
            int unspent = Math.max(0, owned - need);
            if (unspent > 0) {
                surplus.put(item, unspent);
            } else {
                surplus.remove(item);
            }
            JsonObject row = new JsonObject();
            row.addProperty("unit", TOOLS.contains(item) ? "durability" : "items");
            row.addProperty("required", need);
            row.addProperty("npc", owned);
            row.addProperty("warehouse", supplied);
            row.addProperty("missing", Math.max(0, need - available));
            row.addProperty("remaining_after_build", Math.max(0, available - need));
            rows.add(id(item), row);
        });

        Map<String, Integer> drops = new LinkedHashMap<>();
        for (JsonElement element : actions) {
            JsonObject action = element.getAsJsonObject();
            if ("removed".equals(action.get("status").getAsString()) && action.has("interaction_evidence")) {
                for (JsonElement entry : action.getAsJsonObject("interaction_evidence").getAsJsonArray("drops")) {
                    JsonObject drop = entry.getAsJsonObject();
                    drops.merge(drop.get("id").getAsString(), drop.get("count").getAsInt(), Integer::sum);
                }
            }
        }
        JsonObject recovered = new JsonObject();
        drops.forEach(recovered::addProperty);
        JsonObject result = new JsonObject();
        result.add("requirements", rows);
        result.add("warehouse_items", items(chest));
        result.add("npc_items", items(npc));
        result.add("npc_surplus", jsonTotals(surplus));
        result.add("estimated_recoverable", jsonTotals(recoverable));
        result.add("recorded_ground_drops", recovered);
        return result;
    }

    private static void estimateDrops(ServerLevel level, WorkStep step, Map<Item, Integer> totals) {
        var table = level.getServer().getLootData().getLootTable(step.before().getBlock().getLootTable());
        var params = new LootParams.Builder(level).withParameter(LootContextParams.BLOCK_STATE, step.before())
                .withParameter(LootContextParams.ORIGIN, Vec3.atCenterOf(step.pos()))
                .withParameter(LootContextParams.TOOL, item(step) == Items.AIR ? ItemStack.EMPTY : item(step).getDefaultInstance())
                .create(LootContextParamSets.BLOCK);
        // This fixed-seed estimate stays outside cargo; actual break events record the real drops.
        var context = new LootContext.Builder(params).withOptionalRandomSeed(step.pos().asLong() | 1).create(null);
        table.getRandomItemsRaw(context, stack -> {
            if (!stack.isEmpty()) {
                totals.merge(stack.getItem(), stack.getCount(), Integer::sum);
            }
        });
    }

    static String shortages(JsonObject summary) {
        List<String> shortages = new ArrayList<>();
        summary.getAsJsonObject("requirements").entrySet().forEach(entry -> {
            JsonObject row = entry.getValue().getAsJsonObject();
            int count = row.get("missing").getAsInt();
            if (count > 0) {
                String unit = "durability".equals(row.get("unit").getAsString()) ? " 耐久" : "";
                shortages.add(entry.getKey() + " ×" + count + unit);
            }
        });
        return shortages.isEmpty() ? "当前无材料缺口" : "缺料 " + String.join("、", shortages);
    }

    private record Move(int slot, ItemStack stack) {}

    static final class Transfer {
        private final List<Move> moves;
        final JsonObject evidence;

        private Transfer(List<Move> moves, JsonObject evidence) {
            this.moves = moves;
            this.evidence = evidence;
        }

        void apply(ChestBlockEntity chest, SimpleContainer inventory) {
            String sourceBefore = evidence.get("warehouse_before_sha256").getAsString();
            String npcBefore = evidence.get("npc_before_sha256").getAsString();
            if (!sourceBefore.equals(digest(chest)) || !npcBefore.equals(cargoDigest(inventory))) {
                throw Wire.conflict("warehouse_changed", "搬运前库存已变化。");
            }
            SimpleContainer originalChest = copy(chest);
            SimpleContainer originalCargo = copy(inventory);
            var source = new InvWrapper(chest);
            try {
                for (Move move : moves) {
                    ItemStack taken = source.extractItem(move.slot(), move.stack().getCount(), false);
                    if (!ItemStack.matches(taken, move.stack()) || !inventory.addItem(taken).isEmpty()) {
                        throw new IllegalStateException("Warehouse extraction or insertion differed from its simulation");
                    }
                }
                if (!evidence.get("warehouse_after_sha256").getAsString().equals(digest(chest))
                        || !evidence.get("npc_after_sha256").getAsString().equals(cargoDigest(inventory))) {
                    throw new IllegalStateException("Warehouse transfer did not establish the expected stock");
                }
                chest.setChanged();
                inventory.setChanged();
            } catch (RuntimeException error) {
                // Both containers are on the game thread. Restore the captured stacks before halting.
                for (int slot = 0; slot < originalChest.getContainerSize(); slot++) {
                    chest.setItem(slot, originalChest.getItem(slot));
                }
                for (int slot = 0; slot < originalCargo.getContainerSize(); slot++) {
                    inventory.setItem(slot, originalCargo.getItem(slot));
                }
                chest.setChanged();
                inventory.setChanged();
                ForgeBridge.LOGGER.warn("Warehouse transfer was rolled back", error);
                throw Wire.conflict("warehouse_transfer_mismatch", "取料核验失败，已停止施工。");
            }
        }
    }

    static void requireNextItem(ChestBlockEntity chest, SimpleContainer cargo, WorkStep step) {
        Item next = item(step);
        int slot = find(chest, next);
        if (slot < 0) {
            throw Wire.conflict(step.remove() ? "out_of_tool" : "out_of_material", "供料箱缺少 " + id(next));
        }
        if (!cargo.canAddItem(chest.getItem(slot).copyWithCount(1))) {
            throw Wire.conflict("inventory_full", "NPC 背包没有可用空间。");
        }
    }

    static Transfer planTransfer(ChestBlockEntity chest, SimpleContainer inventory, List<WorkStep> steps,
                                 int cursor, int batchSize) {
        SimpleContainer pendingChest = copy(chest);
        SimpleContainer pendingNpc = copy(inventory);
        Map<Item, Integer> remainingCargo = quantities(inventory, true);
        Map<Item, Integer> taken = new LinkedHashMap<>();
        List<Move> moves = new ArrayList<>();
        for (int index = cursor; index < steps.size() && moves.size() < batchSize; index++) {
            WorkStep step = steps.get(index);
            if (step.before() == step.after()) {
                continue;
            }
            if (index > cursor && (steps.get(cursor).remove() || step.remove())) {
                break;
            }
            Item item = item(step);
            if (item == Items.AIR) {
                continue;
            }
            int available = remainingCargo.getOrDefault(item, 0);
            if (available > 0) {
                remainingCargo.put(item, available - 1);
                continue;
            }
            int slot = find(pendingChest, item);
            if (slot < 0) {
                if (index == cursor) {
                    throw Wire.conflict(step.remove() ? "out_of_tool" : "out_of_material",
                            "供料箱缺少 " + id(item));
                }
                break;
            }
            ItemStack portion = pendingChest.getItem(slot).copyWithCount(1);
            if (!pendingNpc.canAddItem(portion)) {
                if (index == cursor) {
                    throw Wire.conflict("inventory_full", "NPC 背包没有可用空间。");
                }
                break;
            }
            pendingChest.removeItem(slot, 1);
            if (!pendingNpc.addItem(portion.copy()).isEmpty()) {
                throw Wire.conflict("inventory_full", "NPC 背包没有可用空间。");
            }
            moves.add(new Move(slot, portion));
            taken.merge(item, 1, Integer::sum);
        }

        JsonObject evidence = new JsonObject();
        evidence.addProperty("method", "ServerPlayerGameMode.useItemOn + Forge InvWrapper.extractItem + SimpleContainer.addItem");
        evidence.addProperty("warehouse_before_sha256", digest(chest));
        evidence.addProperty("warehouse_after_sha256", digest(pendingChest));
        evidence.addProperty("npc_before_sha256", cargoDigest(inventory));
        evidence.addProperty("npc_after_sha256", cargoDigest(pendingNpc));
        evidence.add("warehouse_before", items(chest));
        evidence.add("warehouse_after", items(pendingChest));
        evidence.add("npc_before", items(inventory));
        evidence.add("npc_after", items(pendingNpc));
        evidence.add("moved", jsonTotals(taken));
        return new Transfer(moves, evidence);
    }

    static int find(Container storage, Item item) {
        for (int slot = 0; slot < storage.getContainerSize(); slot++) {
            ItemStack stack = storage.getItem(slot);
            if (stack.is(item) && usable(stack) > 0) {
                return slot;
            }
        }
        return -1;
    }
}
