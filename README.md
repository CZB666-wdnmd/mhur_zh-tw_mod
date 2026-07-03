# 僕のヒーローアカデミア ULTRA RUMBLE 繁體中文漢化模組

這是一個為《僕のヒーローアカデミア ULTRA RUMBLE》（My Hero Ultra Rumble / 我的英雄學院 ULTRA RUMBLE）製作的繁體中文漢化模組。

## 重要更新說明

**每次遊戲發生更新時（包含 Steam 的遊戲更新以及遊戲內的熱更新），本模組可能會失效或導致遊戲無法運行。**
* 遇到遊戲更新時，請**等待我適配新版本並發布更新**。
* 待我發布新版本後，請重新下載最新的安裝檔並**重新安裝**即可。

## 字體使用

本漢化模組的文本顯示使用了以下開源字體：
* [思源黑體 (Source Han Sans)](https://github.com/adobe-fonts/source-han-sans)
* [思源宋體 (Source Han Serif)](https://github.com/adobe-fonts/source-han-serif)

## 安裝說明

為了方便使用，我已經將 Python 安裝腳本打包成了可執行的 `.exe` 檔案。

1. 前往本倉庫的 **[Releases](https://github.com/CZB666-wdnmd/mhur_zh-tw_mod/releases)** 頁面。
2. 下載最新版本的 `.exe` 安裝檔。
3. **強烈建議：** 在安裝前，請先完全關閉 Steam 客戶端（包含右下角系統匣的 Steam 圖標）。
4. 直接雙擊運行下載的 `.exe` 檔案，安裝器會自動執行以下操作：
   * 檢測你的遊戲版本是否與當前模組匹配。
   * 自動將所需的 `.pak` 和 `master.db` 文件複製到遊戲目錄。
   * 自動將 Steam 中該遊戲的語言設置為「日本語」（因漢化是基於日文文本進行替換）。
   * 自動為遊戲添加 `-fileopenlog` 啟動參數（加載模組必須）。
5. 看到「全部流程結束」的提示後，關閉視窗，打開 Steam 啟動遊戲即可享受中文介面！

> **備註：** 如果安裝器因權限等原因無法自動設置啟動參數或語言，請依照安裝器視窗內的提示，手動在 Steam 遊戲屬性中將語言改為「日本語」，並在啟動選項中填入 `-fileopenlog`。

## 翻譯意見與問題反饋

如果你在遊戲中發現任何翻譯錯誤、漏翻，或者有更道地的翻譯建議，歡迎透過提交 **[Issue](https://github.com/CZB666-wdnmd/mhur_zh-tw_mod/issues)** 的方式來告訴我！請盡量附上遊戲內的截圖，這會幫助我更快定位問題。

## 開源聲明

請注意，本倉庫**僅開源 Python 安裝器腳本**（`install_mhur_mod.py`）。
* 任何人皆可查看、學習或改進該安裝器的代碼。
* **不提供**模組打包資源的源文件（如未打包的文本、貼圖或原始解包文件）。

---

## For Other Mod Developers

If you are a mod developer and are interested in learning about the translation process, localization structure, or packing methods used for *My Hero Ultra Rumble*, feel free to contact me! I would be happy to share insights on how the localization works. You can reach out by opening an issue or contacting me directly.
