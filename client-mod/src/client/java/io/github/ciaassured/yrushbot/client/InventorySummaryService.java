package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

public final class InventorySummaryService {
    public JsonObject summarize(Inventory inventory) {
        Summary summary = new Summary();

        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            ItemStack stack = inventory.getItem(slot);
            if (!stack.isEmpty()) {
                summary.accept(stack);
            }
        }

        return summary.toJson();
    }

    private static String itemId(Item item) {
        Identifier id = BuiltInRegistries.ITEM.getKey(item);
        return id.toString();
    }

    private static final class Summary {
        private int placeableBlocks;
        private int logs;
        private int planks;
        private int sticks;
        private int craftingTables;
        private int dirtLikeBlocks;
        private int stoneLikeBlocks;
        private int woodLikeBlocks;

        private ToolTier bestPickaxe = ToolTier.NONE;
        private ToolTier bestAxe = ToolTier.NONE;
        private ToolTier bestShovel = ToolTier.NONE;

        private void accept(ItemStack stack) {
            Item item = stack.getItem();
            int count = stack.getCount();
            String id = itemId(item);

            if (item instanceof BlockItem && isUsefulPlaceableBlock(id)) {
                placeableBlocks += count;
            }
            if (isLog(id)) {
                logs += count;
                woodLikeBlocks += count;
            }
            if (id.endsWith("_planks")) {
                planks += count;
                woodLikeBlocks += count;
            }
            if (item == Items.STICK) {
                sticks += count;
            }
            if (item == Items.CRAFTING_TABLE) {
                craftingTables += count;
            }
            if (isDirtLike(id)) {
                dirtLikeBlocks += count;
            }
            if (isStoneLike(id)) {
                stoneLikeBlocks += count;
            }

            bestPickaxe = bestPickaxe.max(toolTier(id, "_pickaxe"));
            bestAxe = bestAxe.max(toolTier(id, "_axe"));
            bestShovel = bestShovel.max(toolTier(id, "_shovel"));
        }

        private JsonObject toJson() {
            JsonObject json = new JsonObject();
            json.addProperty("placeable_blocks", placeableBlocks);
            json.addProperty("logs", logs);
            json.addProperty("planks", planks);
            json.addProperty("sticks", sticks);
            json.addProperty("crafting_tables", craftingTables);
            json.addProperty("dirt_like_blocks", dirtLikeBlocks);
            json.addProperty("stone_like_blocks", stoneLikeBlocks);
            json.addProperty("wood_like_blocks", woodLikeBlocks);
            json.addProperty("has_pickaxe", bestPickaxe != ToolTier.NONE);
            json.addProperty("best_pickaxe", bestPickaxe.label());
            json.addProperty("has_axe", bestAxe != ToolTier.NONE);
            json.addProperty("best_axe", bestAxe.label());
            json.addProperty("has_shovel", bestShovel != ToolTier.NONE);
            json.addProperty("best_shovel", bestShovel.label());
            return json;
        }

        private static boolean isUsefulPlaceableBlock(String id) {
            return !id.endsWith(":air")
                && !id.contains("water")
                && !id.contains("lava")
                && !id.contains("fire")
                && !id.contains("torch")
                && !id.contains("button")
                && !id.contains("pressure_plate")
                && !id.contains("sign")
                && !id.contains("banner")
                && !id.contains("bed")
                && !id.contains("door")
                && !id.contains("trapdoor");
        }

        private static boolean isLog(String id) {
            return id.endsWith("_log")
                || id.endsWith("_stem")
                || id.endsWith("_hyphae")
                || id.endsWith(":bamboo_block");
        }

        private static boolean isDirtLike(String id) {
            return id.endsWith(":dirt")
                || id.endsWith(":coarse_dirt")
                || id.endsWith(":rooted_dirt")
                || id.endsWith(":grass_block")
                || id.endsWith(":podzol")
                || id.endsWith(":mycelium")
                || id.endsWith(":sand")
                || id.endsWith(":red_sand")
                || id.endsWith(":gravel");
        }

        private static boolean isStoneLike(String id) {
            return id.endsWith(":stone")
                || id.endsWith(":cobblestone")
                || id.endsWith(":deepslate")
                || id.endsWith(":cobbled_deepslate")
                || id.endsWith(":granite")
                || id.endsWith(":diorite")
                || id.endsWith(":andesite")
                || id.endsWith(":tuff")
                || id.endsWith(":calcite")
                || id.endsWith(":basalt")
                || id.endsWith(":blackstone");
        }

        private static ToolTier toolTier(String id, String suffix) {
            if (!id.endsWith(suffix)) {
                return ToolTier.NONE;
            }
            if (id.contains("netherite")) {
                return ToolTier.NETHERITE;
            }
            if (id.contains("diamond")) {
                return ToolTier.DIAMOND;
            }
            if (id.contains("iron")) {
                return ToolTier.IRON;
            }
            if (id.contains("stone")) {
                return ToolTier.STONE;
            }
            if (id.contains("golden")) {
                return ToolTier.GOLDEN;
            }
            if (id.contains("wooden")) {
                return ToolTier.WOODEN;
            }
            return ToolTier.NONE;
        }
    }

    private enum ToolTier {
        NONE("none", 0),
        WOODEN("wooden", 1),
        GOLDEN("golden", 1),
        STONE("stone", 2),
        IRON("iron", 3),
        DIAMOND("diamond", 4),
        NETHERITE("netherite", 5);

        private final String label;
        private final int rank;

        ToolTier(String label, int rank) {
            this.label = label;
            this.rank = rank;
        }

        private String label() {
            return label;
        }

        private ToolTier max(ToolTier other) {
            return other.rank > rank ? other : this;
        }
    }
}
