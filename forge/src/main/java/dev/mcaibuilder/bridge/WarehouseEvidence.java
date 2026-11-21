package dev.mcaibuilder.bridge;

import net.minecraft.core.BlockPos;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.event.entity.player.PlayerContainerEvent;
import net.minecraftforge.event.entity.player.PlayerInteractEvent;
import net.minecraftforge.eventbus.api.Event;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID)
final class WarehouseEvidence {
    private static final ThreadLocal<WarehouseEvidence> ACTIVE = new ThreadLocal<>();

    private final FakePlayer actor;
    private final BlockPos position;
    int rightClicks;
    int menus;
    boolean denied;

    private WarehouseEvidence(FakePlayer actor, BlockPos position) {
        this.actor = actor;
        this.position = position;
    }

    static WarehouseEvidence begin(FakePlayer actor, BlockPos pos) {
        WarehouseEvidence evidence = new WarehouseEvidence(actor, pos);
        ACTIVE.set(evidence);
        return evidence;
    }

    static void end() {
        ACTIVE.remove();
    }

    @SubscribeEvent(priority = EventPriority.LOWEST, receiveCanceled = true)
    public static void rightClick(PlayerInteractEvent.RightClickBlock event) {
        WarehouseEvidence evidence = ACTIVE.get();
        if (evidence != null && event.getEntity() == evidence.actor && event.getPos().equals(evidence.position)) {
            evidence.rightClicks++;
            evidence.denied |= event.isCanceled() || event.getUseBlock() == Event.Result.DENY;
        }
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public static void open(PlayerContainerEvent.Open event) {
        WarehouseEvidence evidence = ACTIVE.get();
        if (evidence != null && event.getEntity() == evidence.actor) {
            evidence.menus++;
        }
    }
}
