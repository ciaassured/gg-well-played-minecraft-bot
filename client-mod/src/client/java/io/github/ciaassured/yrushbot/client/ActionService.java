package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.util.Mth;

public final class ActionService {
    private final MovementState movement = new MovementState();

    private boolean attack;
    private boolean attackControlled;
    private boolean use;
    private boolean useControlled;
    private Integer selectedSlot;
    private double pendingYawDelta;
    private double pendingPitchDelta;
    private boolean releaseRequested;

    public void handleMessage(JsonObject message) {
        String type = stringValue(message, "type", "");
        if ("release_all".equals(type)) {
            releaseAll();
            return;
        }
        if (!"action".equals(type)) {
            return;
        }

        JsonObject movementObject = objectValue(message, "movement");
        if (movementObject != null) {
            movement.update(movementObject);
        }

        JsonObject lookObject = objectValue(message, "look");
        if (lookObject != null) {
            pendingYawDelta += doubleValue(lookObject, "yaw_delta", 0.0);
            pendingPitchDelta += doubleValue(lookObject, "pitch_delta", 0.0);
        }

        if (message.has("attack")) {
            attack = message.get("attack").getAsBoolean();
            attackControlled = true;
        }
        if (message.has("use")) {
            use = message.get("use").getAsBoolean();
            useControlled = true;
        }
        if (message.has("selected_slot")) {
            int slot = message.get("selected_slot").getAsInt();
            if (slot >= 0 && slot <= 8) {
                selectedSlot = slot;
            }
        }
    }

    public void apply(Minecraft client) {
        if (releaseRequested) {
            releaseRequested = false;
            movement.release(client);
            setKey(client.options.keyAttack, false);
            setKey(client.options.keyUse, false);
        }

        movement.apply(client);
        if (attackControlled) {
            setKey(client.options.keyAttack, attack);
        }
        if (useControlled) {
            setKey(client.options.keyUse, use);
        }

        LocalPlayer player = client.player;
        if (player == null) {
            return;
        }

        if (selectedSlot != null) {
            player.getInventory().setSelectedSlot(selectedSlot);
            selectedSlot = null;
        }

        applyLook(player);
    }

    public void releaseAll() {
        movement.clear();
        attack = false;
        attackControlled = false;
        use = false;
        useControlled = false;
        selectedSlot = null;
        pendingYawDelta = 0.0;
        pendingPitchDelta = 0.0;
        releaseRequested = true;
    }

    private void applyLook(LocalPlayer player) {
        if (pendingYawDelta == 0.0 && pendingPitchDelta == 0.0) {
            return;
        }

        float yaw = player.getYRot() + (float) pendingYawDelta;
        float pitch = Mth.clamp(player.getXRot() + (float) pendingPitchDelta, -90.0F, 90.0F);
        player.setYRot(yaw);
        player.setXRot(pitch);
        player.yHeadRot = yaw;

        pendingYawDelta = 0.0;
        pendingPitchDelta = 0.0;
    }

    private static void setKey(KeyMapping key, boolean down) {
        key.setDown(down);
    }

    private static JsonObject objectValue(JsonObject object, String key) {
        JsonElement value = object.get(key);
        return value != null && value.isJsonObject() ? value.getAsJsonObject() : null;
    }

    private static String stringValue(JsonObject object, String key, String fallback) {
        JsonElement value = object.get(key);
        return value == null ? fallback : value.getAsString();
    }

    private static double doubleValue(JsonObject object, String key, double fallback) {
        JsonElement value = object.get(key);
        return value == null ? fallback : value.getAsDouble();
    }

    private static final class MovementState {
        private boolean forward;
        private boolean back;
        private boolean left;
        private boolean right;
        private boolean jump;
        private boolean sneak;
        private boolean sprint;
        private boolean controlled;

        private void update(JsonObject object) {
            controlled = true;
            forward = booleanValue(object, "forward", forward);
            back = booleanValue(object, "back", back);
            left = booleanValue(object, "left", left);
            right = booleanValue(object, "right", right);
            jump = booleanValue(object, "jump", jump);
            sneak = booleanValue(object, "sneak", sneak);
            sprint = booleanValue(object, "sprint", sprint);
        }

        private void apply(Minecraft client) {
            if (!controlled) {
                return;
            }
            setKey(client.options.keyUp, forward);
            setKey(client.options.keyDown, back);
            setKey(client.options.keyLeft, left);
            setKey(client.options.keyRight, right);
            setKey(client.options.keyJump, jump);
            setKey(client.options.keyShift, sneak);
            setKey(client.options.keySprint, sprint);
        }

        private void release(Minecraft client) {
            setKey(client.options.keyUp, false);
            setKey(client.options.keyDown, false);
            setKey(client.options.keyLeft, false);
            setKey(client.options.keyRight, false);
            setKey(client.options.keyJump, false);
            setKey(client.options.keyShift, false);
            setKey(client.options.keySprint, false);
        }

        private void clear() {
            forward = false;
            back = false;
            left = false;
            right = false;
            jump = false;
            sneak = false;
            sprint = false;
            controlled = false;
        }

        private static boolean booleanValue(JsonObject object, String key, boolean fallback) {
            JsonElement value = object.get(key);
            return value == null ? fallback : value.getAsBoolean();
        }
    }
}
