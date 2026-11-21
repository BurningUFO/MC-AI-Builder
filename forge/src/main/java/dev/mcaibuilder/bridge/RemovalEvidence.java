package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.event.entity.EntityJoinLevelEvent;
import net.minecraftforge.event.entity.player.PlayerInteractEvent;
import net.minecraftforge.event.level.BlockEvent;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.registries.ForgeRegistries;

@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID)
final class RemovalEvidence {
    private static final ThreadLocal<RemovalEvidence> ACTIVE = new ThreadLocal<>();

    private final FakePlayer actor;
    private final BlockPos position;
    private final BlockState before;
    final JsonArray drops = new JsonArray();
    int leftClicks;
    int breakEvents;
    boolean rejected;

    private RemovalEvidence(FakePlayer actor, WorkStep step) {
        this.actor = actor;
        position = step.pos();
        before = step.before();
    }

    static RemovalEvidence begin(FakePlayer actor, WorkStep step) {
        RemovalEvidence attempt = new RemovalEvidence(actor, step);
        ACTIVE.set(attempt);
        return attempt;
    }

    static void end() {
        ACTIVE.remove();
    }

    @SubscribeEvent(priority = EventPriority.LOWEST, receiveCanceled = true)
    public static void onLeftClick(PlayerInteractEvent.LeftClickBlock event) {
        RemovalEvidence attempt = ACTIVE.get();
        if (attempt != null && event.getEntity() == attempt.actor && event.getPos().equals(attempt.position)) {
            attempt.leftClicks++;
        }
    }

    @SubscribeEvent(priority = EventPriority.LOWEST, receiveCanceled = true)
    public static void onBreak(BlockEvent.BreakEvent event) {
        RemovalEvidence attempt = ACTIVE.get();
        if (attempt == null || event.getPlayer() != attempt.actor) {
            return;
        }
        attempt.breakEvents++;
        if (!event.getPos().equals(attempt.position) || event.getState() != attempt.before) {
            event.setCanceled(true);
        }
        attempt.rejected |= event.isCanceled();
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public static void onItem(EntityJoinLevelEvent event) {
        RemovalEvidence attempt = ACTIVE.get();
        if (attempt != null && event.getLevel() == attempt.actor.level() && event.getEntity() instanceof ItemEntity drop
                && drop.position().distanceToSqr(Vec3.atCenterOf(attempt.position)) <= 2.25) {
            ItemStack stack = drop.getItem();
            JsonObject data = new JsonObject();
            data.addProperty("id", ForgeRegistries.ITEMS.getKey(stack.getItem()).toString());
            data.addProperty("count", stack.getCount());
            attempt.drops.add(data);
        }
    }
}
