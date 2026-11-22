package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.duzo.players.entities.FakePlayerEntity;
import net.minecraft.client.CameraType;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

/** Exercises the real camera and client tracking on the synthetic acceptance world. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class LocalCameraFixture {
    private static UUID dropOnSwitch;
    private static UUID dropChunkOnSwitch;
    private static int chunkDrops;
    private static int dropped;

    private LocalCameraFixture() {}

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END || !Boolean.getBoolean("mcaibuilder.cameraSmoke")) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        var server = client.getSingleplayerServer();
        Path root = client.gameDirectory.toPath();
        Path file = root.resolve(".mcaibuilder-camera-fixture.json");
        if (server == null || client.player == null || client.level == null || !Files.isRegularFile(file)) {
            return;
        }
        boolean lab = Files.isRegularFile(root.resolve(".mcaibuilder-test"))
                && server.getWorldData().getLevelName().equals("M1 Construction Lab");
        boolean syntheticCopy = CopyProfile.fixture(root) && client.player.getGameProfile().getName().equals("BuilderTester");
        if (!lab && !syntheticCopy) {
            return;
        }
        try {
            JsonObject request = Wire.object(JsonParser.parseString(Files.readString(file)), "camera fixture");
            Files.delete(file);
            String action = Wire.string(request.get("action"), "action");
            JsonObject result = new JsonObject();
            result.add("request", request.get("request"));
            switch (action) {
                case "pose", "world", "panels" -> {
                    if (!lab) {
                        throw Wire.invalid("This action needs the synthetic camera lab");
                    }
                    UUID owner = client.player.getUUID();
                    server.execute(() -> sample(root, server.getPlayerList().getPlayer(owner), request, action, result));
                    return;
                }
                case "third_person" -> client.options.setCameraType(CameraType.THIRD_PERSON_BACK);
                case "drop_on_switch" -> dropOnSwitch = BuildPlan.id(request, "npc");
                case "drop_chunk_on_switch" -> dropChunkOnSwitch = BuildPlan.id(request, "npc");
                case "client" -> { }
                default -> throw Wire.invalid("Unknown camera fixture action");
            }
            result.addProperty("camera_is_player", client.getCameraEntity() == client.player);
            result.addProperty("rendered_camera_is_player",
                    client.gameRenderer.getMainCamera().getEntity() == client.player);
            result.addProperty("camera_type", client.options.getCameraType().name());
            result.addProperty("dimension", client.level.dimension().location().toString());
            result.addProperty("tracked_entity_drops", dropped);
            result.addProperty("observed_chunk_drops", chunkDrops);
            if (request.has("entity_id") && client.level.getEntity(Wire.integer(request.get("entity_id"), "entity ID"))
                    instanceof FakePlayerEntity tracked) {
                result.addProperty("tracked_yaw", tracked.getViewYRot(1));
                result.addProperty("tracked_pitch", tracked.getViewXRot(1));
            }
            result.add("position", NpcObservations.position(client.player.position()));
            JobJournal.write(root.resolve(".mcaibuilder-camera-proof.json"), result);
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Isolated camera fixture failed", error);
        }
    }

    @SubscribeEvent
    public static void render(TickEvent.RenderTickEvent event) {
        if (event.phase != TickEvent.Phase.START || dropOnSwitch == null && dropChunkOnSwitch == null) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        if (client.level == null || !(client.getCameraEntity() instanceof FakePlayerEntity target)) {
            return;
        }
        if (target.getUUID().equals(dropChunkOnSwitch)) {
            dropChunkOnSwitch = null;
            BlockPos adjacent = target.blockPosition().east(4);
            if ((adjacent.getX() >> 4) != (target.getBlockX() >> 4) && client.level.hasChunkAt(adjacent)) {
                client.level.getChunkSource().drop(adjacent.getX() >> 4, adjacent.getZ() >> 4);
                chunkDrops++;
            }
        }
        if (target.getUUID().equals(dropOnSwitch)) {
            dropOnSwitch = null;
            client.level.removeEntity(target.getId(), Entity.RemovalReason.DISCARDED);
            dropped++;
        }
    }

    private static void sample(Path root, ServerPlayer owner, JsonObject request, String action, JsonObject result) {
        Path proof = root.resolve(".mcaibuilder-camera-proof.json");
        try {
            if (owner == null || !owner.getServer().isSingleplayerOwner(owner.getGameProfile())) {
                throw Wire.invalid("The isolated owner is required");
            }
            BuildSite site = BuildSite.read(root.resolve("config").resolve("mcaibuilder.json"));
            var level = owner.serverLevel();
            site.requireLoaded(level);
            if (action.equals("panels")) {
                if (owner.gameMode.getGameModeForPlayer() != GameType.CREATIVE) {
                    throw Wire.invalid("The reference scene needs the isolated creative player");
                }
                int placed = panel(owner, site, 8, 10.5, Blocks.BLUE_STAINED_GLASS)
                        + panel(owner, site, 14, 12.5, Blocks.OAK_PLANKS);
                result.addProperty("native_reference_placements", placed);
                JobJournal.write(proof, result);
                return;
            }
            if (!(level.getEntity(BuildPlan.id(request, "npc")) instanceof FakePlayerEntity target)
                    || !target.getTags().contains(ForgeBridge.MOD_ID) || !site.area().contains(target.blockPosition())) {
                throw Wire.invalid("Expected the fixture worker in its approved area");
            }
            if (action.equals("pose")) {
                int yaw = Wire.integer(request.get("yaw"), "yaw");
                int pitch = Wire.integer(request.get("pitch"), "pitch");
                if (Math.abs(yaw) > 180 || Math.abs(pitch) > 90) {
                    throw Wire.invalid("Camera fixture pose outside the supported range");
                }
                target.getNavigation().stop();
                target.setYRot(yaw);
                target.setYHeadRot(yaw);
                target.setYBodyRot(yaw);
                target.setXRot(pitch);
            } else {
                JsonArray blocks = new JsonArray();
                for (BlockPos pos : BlockPos.betweenClosed(site.area().halo().min(), site.area().halo().max())) {
                    blocks.add(Wire.state(level.getBlockState(pos)));
                }
                result.addProperty("blocks_sha256", Wire.sha256(blocks));
                result.add("inventory", Warehouse.items(target.getInventory()));
            }
            result.addProperty("entity_id", target.getId());
            result.add("npc_position", NpcObservations.position(target.position()));
            result.add("npc_eye", NpcObservations.position(target.getEyePosition()));
            result.addProperty("yaw", target.getViewYRot(1));
            result.addProperty("pitch", target.getViewXRot(1));
            result.add("player_position", NpcObservations.position(owner.position()));
            result.addProperty("game_tick", level.getGameTime());
            JobJournal.write(proof, result);
        } catch (IOException | RuntimeException error) {
            ForgeBridge.LOGGER.warn("Isolated camera state sample failed", error);
        }
    }

    private static int panel(ServerPlayer player, BuildSite site, int z, double stanceZ, Block block) {
        var level = player.serverLevel();
        ItemStack saved = player.getMainHandItem().copy();
        int placed = 0;
        try {
            for (int x = 10; x <= 16; x++) {
                for (int y = -60; y < -57; y++) {
                    BlockPos pos = new BlockPos(x, y, z);
                    if (!site.canBuildAt(pos) || !level.getBlockState(pos).isAir()) {
                        throw Wire.invalid("Reference panel position is outside the empty test area");
                    }
                    player.teleportTo(level, x + .5, y, stanceZ, 0, 0);
                    var eye = player.getEyePosition();
                    Vec3 contact = Vec3.atBottomCenterOf(pos).add(0, -.01, 0);
                    var hit = level.clip(new ClipContext(eye, contact, ClipContext.Block.OUTLINE,
                            ClipContext.Fluid.NONE, player));
                    if (hit.getType() != HitResult.Type.BLOCK || hit.getDirection() != Direction.UP
                            || !hit.getBlockPos().equals(pos.below()) || eye.distanceToSqr(hit.getLocation()) > 4.5 * 4.5) {
                        throw Wire.invalid("The fixture player cannot reach the reference support");
                    }
                    ItemStack stack = new ItemStack(block);
                    player.setItemInHand(InteractionHand.MAIN_HAND, stack);
                    if (!player.gameMode.useItemOn(player, level, stack, InteractionHand.MAIN_HAND, hit).consumesAction()
                            || level.getBlockState(pos) != block.defaultBlockState()) {
                        throw Wire.invalid("Native reference panel placement was rejected");
                    }
                    placed++;
                }
            }
        } finally {
            player.setItemInHand(InteractionHand.MAIN_HAND, saved);
        }
        return placed;
    }

}
