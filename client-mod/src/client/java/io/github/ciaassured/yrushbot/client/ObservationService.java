package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

public final class ObservationService {
    public JsonObject capture(Minecraft client) {
        JsonObject observation = Protocol.baseMessage("observation");
        observation.addProperty("tick", client.level == null ? 0L : client.level.getGameTime());
        observation.add("screen", captureScreen(client.screen));
        observation.add("player", capturePlayer(client.player));
        observation.add("inventory", captureInventory(client.player));
        return observation;
    }

    private JsonObject captureScreen(Screen screen) {
        JsonObject screenObject = new JsonObject();
        screenObject.addProperty("open", screen != null);
        if (screen != null) {
            screenObject.addProperty("name", screen.getClass().getSimpleName());
        }
        return screenObject;
    }

    private JsonObject capturePlayer(LocalPlayer player) {
        JsonObject playerObject = new JsonObject();
        playerObject.addProperty("present", player != null);
        if (player == null) {
            return playerObject;
        }

        Vec3 velocity = player.getDeltaMovement();
        playerObject.addProperty("x", player.getX());
        playerObject.addProperty("y", player.getY());
        playerObject.addProperty("z", player.getZ());
        playerObject.addProperty("yaw", player.getYRot());
        playerObject.addProperty("pitch", player.getXRot());
        playerObject.addProperty("vx", velocity.x);
        playerObject.addProperty("vy", velocity.y);
        playerObject.addProperty("vz", velocity.z);
        playerObject.addProperty("on_ground", player.onGround());
        playerObject.addProperty("in_water", player.isInWater());
        playerObject.addProperty("in_lava", player.isInLava());
        playerObject.addProperty("health", player.getHealth());
        playerObject.addProperty("hunger", player.getFoodData().getFoodLevel());
        playerObject.addProperty("air", player.getAirSupply());
        return playerObject;
    }

    private JsonObject captureInventory(LocalPlayer player) {
        JsonObject inventoryObject = new JsonObject();
        inventoryObject.addProperty("present", player != null);
        if (player == null) {
            return inventoryObject;
        }

        Inventory inventory = player.getInventory();
        inventoryObject.addProperty("selected_slot", inventory.getSelectedSlot());
        inventoryObject.add("hotbar", captureHotbar(inventory));
        return inventoryObject;
    }

    private JsonArray captureHotbar(Inventory inventory) {
        JsonArray hotbar = new JsonArray();
        for (int slot = 0; slot < 9; slot++) {
            ItemStack stack = inventory.getItem(slot);
            if (stack.isEmpty()) {
                continue;
            }

            JsonObject item = new JsonObject();
            item.addProperty("slot", slot);
            item.addProperty("item", itemId(stack));
            item.addProperty("count", stack.getCount());
            hotbar.add(item);
        }
        return hotbar;
    }

    private String itemId(ItemStack stack) {
        Identifier id = BuiltInRegistries.ITEM.getKey(stack.getItem());
        return id.toString();
    }
}
