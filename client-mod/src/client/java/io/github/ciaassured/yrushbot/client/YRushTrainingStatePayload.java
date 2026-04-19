package io.github.ciaassured.yrushbot.client;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

public record YRushTrainingStatePayload(String json) implements CustomPacketPayload {
    public static final Identifier ID = Identifier.fromNamespaceAndPath("yrush", "training_state");
    public static final Type<YRushTrainingStatePayload> TYPE = new Type<>(ID);
    public static final StreamCodec<RegistryFriendlyByteBuf, YRushTrainingStatePayload> CODEC = StreamCodec.of(
        (buffer, payload) -> buffer.writeUtf(payload.json()),
        buffer -> new YRushTrainingStatePayload(buffer.readUtf())
    );

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
