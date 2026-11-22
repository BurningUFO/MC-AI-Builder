package dev.mcaibuilder.bridge;

import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.network.chat.Component;
import net.minecraftforge.event.RegisterCommandsEvent;


final class ArchitectCommand {
    private ArchitectCommand() {}

    @FunctionalInterface
    interface Handler {
        void receive(ServerPlayer player, String message, boolean natural);
    }

    static void register(RegisterCommandsEvent event, Handler handler) {
        event.getDispatcher().register(Commands.literal("architect")
                .executes(ctx -> {
                    ctx.getSource().sendSystemMessage(Component.literal(
                            "[Architect] @Builder <需求>  |  /architect link  |  /architect observe  |  /architect approve <确认码>"));
                    return 1;
                })
                .then(Commands.literal("ask")
                        .then(Commands.argument("message", StringArgumentType.greedyString())
                                .executes(ctx -> run(ctx, handler, true))))
                .then(Commands.argument("message", StringArgumentType.greedyString())
                        .executes(ctx -> run(ctx, handler, false))));
    }

    private static int run(CommandContext<CommandSourceStack> ctx, Handler handler,
                           boolean naturalOnly) throws CommandSyntaxException {
        var player = ctx.getSource().getPlayerOrException();
        try {
            handler.receive(player, StringArgumentType.getString(ctx, "message"), naturalOnly);
            return 1;
        } catch (Wire.Problem failure) {
            ctx.getSource().sendFailure(Component.literal(
                    "[Architect] " + failure.getMessage() + " [" + failure.code + "]"));
            return 0;
        }
    }
}
