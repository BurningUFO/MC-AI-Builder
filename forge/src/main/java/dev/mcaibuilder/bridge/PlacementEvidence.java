package dev.mcaibuilder.bridge;

import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.event.entity.player.PlayerInteractEvent;
import net.minecraftforge.event.level.BlockEvent;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID)
final class PlacementEvidence {
    private static final ThreadLocal<PlacementEvidence> ACTIVE = new ThreadLocal<>();

    private final FakePlayer actor;
    private final BuildPlan.Target target;
    int rightClicks;
    int placeEvents;
    boolean rejected;

    private PlacementEvidence(FakePlayer actor, BuildPlan.Target target) {
        this.actor = actor;
        this.target = target;
    }

    static PlacementEvidence begin(FakePlayer actor, BuildPlan.Target target) {
        PlacementEvidence attempt = new PlacementEvidence(actor, target);
        ACTIVE.set(attempt);
        return attempt;
    }

    static void end() {
        ACTIVE.remove();
    }

    @SubscribeEvent(receiveCanceled = true)
    public static void onRightClick(PlayerInteractEvent.RightClickBlock event) {
        PlacementEvidence attempt = ACTIVE.get();
        if (attempt != null && event.getEntity() == attempt.actor) {
            attempt.rightClicks++;
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST, receiveCanceled = true)
    public static void onPlace(BlockEvent.EntityPlaceEvent event) {
        PlacementEvidence attempt = ACTIVE.get();
        if (attempt == null || event.getEntity() != attempt.actor) {
            return;
        }
        attempt.placeEvents++;
        if (event instanceof BlockEvent.EntityMultiPlaceEvent
                || !event.getPos().equals(attempt.target.world())
                || !ConstructionService.matches(event.getPlacedBlock(), attempt.target.state())) {
            event.setCanceled(true);
            attempt.rejected = true;
        }
    }
}
