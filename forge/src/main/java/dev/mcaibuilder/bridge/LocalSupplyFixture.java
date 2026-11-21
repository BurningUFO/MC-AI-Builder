package dev.mcaibuilder.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.inventory.ChestMenu;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.registries.ForgeRegistries;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Supplies fixture-owned test stock through an authorized player's chest menu. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalSupplyFixture {
    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        Path root = client.gameDirectory.toPath();
        boolean copy = CopyProfile.fixture(root);
        boolean practice = Boolean.getBoolean("mcaibuilder.localTest") && Boolean.getBoolean("mcaibuilder.m4Smoke")
                && Files.isRegularFile(root.resolve(".mcaibuilder-test"));
        Path file = root.resolve(".mcaibuilder-stock-request");
        var server = client.getSingleplayerServer();
        if ((!copy && !practice) || server == null || client.player == null || !Files.isRegularFile(file)) {
            return;
        }
        try {
            JsonObject request = Wire.object(JsonParser.parseString(Files.readString(file)), "stock request");
            Files.delete(file);
            var id = client.player.getUUID();
            server.execute(() -> stock(root, server.getPlayerList().getPlayer(id), request, copy));
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Cannot read stock fixture input", error);
        }
    }

    private static void stock(Path root, ServerPlayer player, JsonObject request, boolean copy) {
        JsonObject result = new JsonObject();
        result.add("request", request.get("request"));
        ItemStack hand = null;
        ItemStack transfer = null;
        try {
            BlockPos pos = Wire.position(request.get("position"), "warehouse position");
            if (player == null || player.gameMode.getGameModeForPlayer() != GameType.SURVIVAL) {
                throw new IllegalStateException("The fixture requires a survival test player");
            }
            if (copy) {
                CopyProfile profile = CopyProfile.read(root);
                var game = player.getServer();
                var path = profile.world().resolve("data").resolve("mcaibuilder-site-authorization.json");
                JsonObject state = Wire.object(JsonParser.parseString(Files.readString(path)), "authorization");
                JsonObject active = Wire.object(state.get("active"), "active authorization");
                if (!profile.matches(game) || !profile.owns(game, player)
                        || !profile.id().equals(BuildPlan.id(state, "copy_id"))
                        || !active.get("dimension").getAsString().equals(player.serverLevel().dimension().location().toString())
                        || !BuildSite.Box.read(active.get("area"), "stock area").contains(pos)) {
                    throw new IllegalStateException("Supply fixture must stay within the owner's approved copy area");
                }
                hand = player.getMainHandItem().copy();
                transfer = player.getInventory().getItem(9).copy();
                player.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
                player.getInventory().setItem(9, ItemStack.EMPTY);
            } else {
                BuildSite site = BuildSite.read(root.resolve("config").resolve("mcaibuilder.json"));
                if (site == null || !site.survival() || site.architect() == null || !site.canBuildAt(pos)
                        || pos.getY() != site.spawn().getY() || !site.architect().players().contains(player.getUUID())
                        || !site.world().equals(player.serverLevel().getServer().getWorldData().getLevelName())) {
                    throw new IllegalStateException("Stock fixture is outside the isolated survival site");
                }
            }
            var level = player.serverLevel();
            BlockPos stance = request.has("stance") && copy ? Wire.position(request.get("stance"), "test stance")
                    : pos.offset(0, 0, 2);
            if (copy && (!BuildTerrain.canStand(level, stance) || stance.distSqr(pos) > 9)) {
                throw new IllegalStateException("The copied terrain needs a safe test-player stance by the chest");
            }
            player.teleportTo(level, stance.getX() + 0.5, stance.getY(), stance.getZ() + 0.5, 180, 45);
            if (request.get("place").getAsBoolean()) {
                if (!level.getBlockState(pos).isAir() || !player.getMainHandItem().isEmpty()) {
                    throw new IllegalStateException("Test chest position or player hand is occupied");
                }
                var hit = aim(player, Vec3.atBottomCenterOf(pos).add(0, -0.01, 0));
                ItemStack chest = new ItemStack(Blocks.CHEST);
                player.setItemInHand(InteractionHand.MAIN_HAND, chest);
                InteractionResult placed = player.gameMode.useItemOn(player, level, chest, InteractionHand.MAIN_HAND, hit);
                if (!placed.consumesAction() || !chest.isEmpty()) {
                    throw new IllegalStateException("The test player did not place and consume the test chest item");
                }
                result.addProperty("chest_placed", true);
            }
            if (!(level.getBlockEntity(pos) instanceof ChestBlockEntity chest)) {
                throw new IllegalStateException("The fixture needs a native chest");
            }
            result.add("before", Warehouse.items(chest));
            InteractionResult opened = player.gameMode.useItemOn(player, level, ItemStack.EMPTY,
                    InteractionHand.MAIN_HAND, aim(player, Vec3.atCenterOf(pos)));
            if (!opened.consumesAction() || !(player.containerMenu instanceof ChestMenu menu) || menu.getContainer() != chest) {
                throw new IllegalStateException("The test player could not open the intended chest");
            }
            try {
                for (JsonElement element : Wire.array(request.get("items"), "items")) {
                    JsonObject row = Wire.object(element, "stock row");
                    ResourceLocation key = ResourceLocation.tryParse(Wire.string(row.get("id"), "id"));
                    Item item = key == null ? null : ForgeRegistries.ITEMS.getValue(key);
                    int count = Wire.integer(row.get("count"), "count");
                    if (item == null || count < 1 || count > item.getDefaultInstance().getMaxStackSize()
                            || !player.getInventory().getItem(9).isEmpty()) {
                        throw new IllegalStateException("Invalid fixture test item or occupied transfer slot");
                    }
                    // The testing player supplies this stock. The construction worker never invokes this fixture.
                    ItemStack stack = new ItemStack(item, count);
                    if (row.has("damage")) {
                        int damage = Wire.integer(row.get("damage"), "damage");
                        if (!stack.isDamageableItem() || damage < 0 || damage >= stack.getMaxDamage()) {
                            throw new IllegalStateException("Invalid durability of test tool");
                        }
                        stack.setDamageValue(damage);
                    }
                    player.getInventory().setItem(9, stack);
                    if (menu.quickMoveStack(player, chest.getContainerSize()).isEmpty()
                            || !player.getInventory().getItem(9).isEmpty()) {
                        throw new IllegalStateException("The player's native menu transfer did not fit");
                    }
                }
            } finally {
                player.closeContainer();
            }
            result.addProperty("method", "ServerPlayerGameMode.useItemOn + ChestMenu.quickMoveStack");
            result.add("after", Warehouse.items(chest));
            result.addProperty("success", true);
        } catch (IOException | RuntimeException error) {
            result.addProperty("success", false);
            result.addProperty("error", error.getMessage());
            ForgeBridge.LOGGER.warn("Stock fixture did not complete", error);
        } finally {
            if (hand != null) {
                player.setItemInHand(InteractionHand.MAIN_HAND, hand);
                player.getInventory().setItem(9, transfer);
            }
        }
        try {
            Files.writeString(root.resolve(".mcaibuilder-stock-result"), result.toString());
        } catch (IOException error) {
            ForgeBridge.LOGGER.warn("Could not record player stock transfer", error);
        }
    }

    private static BlockHitResult aim(ServerPlayer player, Vec3 point) {
        var hit = player.serverLevel().clip(new ClipContext(player.getEyePosition(), point, ClipContext.Block.OUTLINE,
                ClipContext.Fluid.NONE, player));
        if (hit.getType() != HitResult.Type.BLOCK || player.getEyePosition().distanceToSqr(hit.getLocation()) > 4.5 * 4.5) {
            throw new IllegalStateException("The test player cannot reach this point");
        }
        return hit;
    }
}
