package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.fml.DistExecutor;
import net.minecraftforge.network.NetworkDirection;
import net.minecraftforge.network.NetworkRegistry;
import net.minecraftforge.network.PacketDistributor;
import net.minecraftforge.network.simple.SimpleChannel;

import java.util.ArrayList;
import java.util.List;

final class GhostPreview {
    private static final int MAX_BLOCKS = 4096;
    private static final String VERSION = "2";
    private static final SimpleChannel CHANNEL = NetworkRegistry.newSimpleChannel(
            ResourceLocation.tryParse(ForgeBridge.MOD_ID + ":ghost_preview"), () -> VERSION,
            NetworkRegistry.acceptMissingOr(VERSION), NetworkRegistry.acceptMissingOr(VERSION));

    private GhostPreview() {}

    static void register() {
        CHANNEL.messageBuilder(Projection.class, 0, NetworkDirection.PLAY_TO_CLIENT)
                .encoder(Projection::write)
                .decoder(Projection::read)
                .consumerMainThread((projection, context) -> DistExecutor.unsafeRunWhenOn(Dist.CLIENT,
                        () -> () -> GhostPreviewClient.show(projection)))
                .add();
        CHANNEL.messageBuilder(ProjectLinks.class, 1, NetworkDirection.PLAY_TO_CLIENT)
                .encoder(ProjectLinks::write)
                .decoder(ProjectLinks::read)
                .consumerMainThread((links, context) -> DistExecutor.unsafeRunWhenOn(Dist.CLIENT,
                        () -> () -> GhostPreviewClient.links(links)))
                .add();
    }

    static boolean sendLinks(ServerPlayer viewer, ProjectLinks links) {
        if (!CHANNEL.isRemotePresent(viewer.connection.connection)) {
            return false;
        }
        CHANNEL.send(PacketDistributor.PLAYER.with(() -> viewer), links);
        return true;
    }

    record ProjectLinks(String name, String image, String scene) {
        ProjectLinks {
            ProjectBlocks.name(name);
            if (!image.matches("[a-z0-9_-]{1,80}\\.png") || !scene.endsWith(".blend")) {
                throw Wire.invalid("Invalid Blender preview filenames");
            }
            ProjectBlocks.name(scene.substring(0, scene.length() - ".blend".length()));
        }

        void write(FriendlyByteBuf buf) {
            buf.writeUtf(name, 96);
            buf.writeUtf(image, 84);
            buf.writeUtf(scene, 102);
        }

        static ProjectLinks read(FriendlyByteBuf buf) {
            return new ProjectLinks(buf.readUtf(96), buf.readUtf(84), buf.readUtf(102));
        }
    }

    static void show(ServerPlayer viewer, JsonObject review) {
        List<Cell> cells = new ArrayList<>();
        for (var element : review.getAsJsonArray("changes")) {
            JsonObject row = element.getAsJsonObject();
            String change = row.get("category").getAsString();
            if (change.equals("keep")) {
                continue;
            }
            BlockState desired = Wire.readState(row.getAsJsonObject("desired"));
            boolean removal = desired.isAir();
            BlockState state = removal ? Wire.readState(row.getAsJsonObject("actual")) : desired;
            if (!state.isAir()) {
                cells.add(new Cell(Wire.position(row.get("world"), "preview.world"), state,
                        removal || change.equals("conflict")));
            }
            if (cells.size() == MAX_BLOCKS) {
                break;
            }
        }
        send(viewer, cells);
    }

    static void clear(ServerPlayer viewer) {
        send(viewer, List.of());
    }

    private static void send(ServerPlayer viewer, List<Cell> cells) {
        if (CHANNEL.isRemotePresent(viewer.connection.connection)) {
            CHANNEL.send(PacketDistributor.PLAYER.with(() -> viewer),
                    new Projection(viewer.level().dimension().location(), cells));
        }
    }

    record Cell(BlockPos pos, BlockState state, boolean removal) {}

    record Projection(ResourceLocation dimension, List<Cell> cells) {
        void write(FriendlyByteBuf buf) {
            buf.writeResourceLocation(dimension);
            buf.writeVarInt(cells.size());
            for (Cell cell : cells) {
                buf.writeBlockPos(cell.pos());
                buf.writeVarInt(Block.getId(cell.state()));
                buf.writeBoolean(cell.removal());
            }
        }

        static Projection read(FriendlyByteBuf buf) {
            ResourceLocation dimension = buf.readResourceLocation();
            int size = buf.readVarInt();
            if (size < 0 || size > MAX_BLOCKS) {
                throw new IllegalArgumentException("Invalid Builder projection size");
            }
            List<Cell> cells = new ArrayList<>(size);
            for (int index = 0; index < size; index++) {
                cells.add(new Cell(buf.readBlockPos(), Block.stateById(buf.readVarInt()), buf.readBoolean()));
            }
            return new Projection(dimension, List.copyOf(cells));
        }
    }
}
