package dev.mcaibuilder.bridge;

import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.ChatFormatting;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.chat.Component;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientChatEvent;
import net.minecraftforge.client.event.ClientPlayerNetworkEvent;
import net.minecraftforge.client.event.RegisterKeyMappingsEvent;
import net.minecraftforge.client.event.ScreenEvent;
import net.minecraftforge.client.settings.KeyConflictContext;
import net.minecraftforge.client.settings.KeyModifier;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import org.lwjgl.glfw.GLFW;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class ArchitectChat {
    private static final Pattern MENTION = Pattern.compile("^@builder(?:\\s+|$)", Pattern.CASE_INSENSITIVE);
    private static final KeyMapping MODE = new KeyMapping("key.mcaibuilder.conversation", KeyConflictContext.IN_GAME,
            KeyModifier.ALT, InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_B, "key.categories.mcaibuilder");

    private static boolean conversation;
    private static boolean shortcutDown;

    private ArchitectChat() {}

    @Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT, bus = Mod.EventBusSubscriber.Bus.MOD)
    public static final class Keys {
        @SubscribeEvent
        public static void register(RegisterKeyMappingsEvent event) {
            event.register(MODE);
        }
    }

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        boolean click = false;
        while (MODE.consumeClick()) {
            click = true;
        }
        boolean held = heldShortcut(client);
        boolean toggle = (held || click) && !shortcutDown;
        shortcutDown = held;
        if (!toggle || client.player == null || !MODE.getKeyConflictContext().isActive()) {
            return;
        }
        if (conversation) {
            conversation = false;
            notify(client, "已退出建筑师对话模式。普通聊天发送到服务器。");
        } else if (supported(client.getConnection())) {
            conversation = true;
            notify(client, "建筑师对话模式已开启。普通文字仅发送给 MC Builder。");
            client.getConnection().sendCommand("architect link");
        } else {
            notify(client, "这个服务器没有启用兼容的建筑师入口。");
        }
    }

    private static boolean heldShortcut(Minecraft client) {
        // Some packs intercept normal key mapping clicks. Check only our configured chord.
        InputConstants.Key key = MODE.getKey();
        if (client.player == null || MODE.isUnbound() || !MODE.isConflictContextAndModifierActive()) {
            return false;
        }
        long window = client.getWindow().getWindow();
        return switch (key.getType()) {
            case KEYSYM -> InputConstants.isKeyDown(window, key.getValue());
            case MOUSE -> GLFW.glfwGetMouseButton(window, key.getValue()) == GLFW.GLFW_PRESS;
            default -> false;
        };
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public static void chat(ClientChatEvent event) {
        // Work from the original submission. Unaddressed messages are left to vanilla and other mods.
        String text = event.getOriginalMessage().strip();
        Matcher target = MENTION.matcher(text);
        boolean addressed = target.find();
        if ((!addressed && !conversation) || text.startsWith("/")) {
            return;
        }
        event.setCanceled(true);
        Minecraft client = Minecraft.getInstance();
        ClientPacketListener connection = client.getConnection();
        if (!supported(connection)) {
            notify(client, "建筑师入口未连接，消息未发送。");
            return;
        }
        String request = addressed ? text.substring(target.end()).strip() : text;
        if (request.isBlank() || request.length() > 240) {
            notify(client, "请输入 1–240 个字符的建筑需求。");
            return;
        }
        connection.sendCommand("architect ask " + request);
    }

    @SubscribeEvent
    public static void logout(ClientPlayerNetworkEvent.LoggingOut event) {
        conversation = false;
        shortcutDown = false;
    }

    @SubscribeEvent
    public static void login(ClientPlayerNetworkEvent.LoggingIn event) {
        conversation = false;
        shortcutDown = false;
    }

    @SubscribeEvent
    public static void render(ScreenEvent.Render.Post event) {
        if (conversation && event.getScreen() instanceof ChatScreen screen) {
            Minecraft client = Minecraft.getInstance();
            event.getGuiGraphics().drawString(client.font, "MC Builder 私聊 · "
                    + MODE.getTranslatedKeyMessage().getString(), 5, screen.height - 28, 0x67E8F9);
        }
    }

    private static boolean supported(ClientPacketListener connection) {
        if (connection == null) {
            return false;
        }
        var architect = connection.getCommands().getRoot().getChild("architect");
        return architect != null && architect.getChild("ask") != null;
    }

    private static void notify(Minecraft client, String message) {
        client.gui.getChat().addMessage(Component.literal("[Architect] MC Builder ").withStyle(ChatFormatting.AQUA)
                .append(Component.literal(message)));
    }
}
