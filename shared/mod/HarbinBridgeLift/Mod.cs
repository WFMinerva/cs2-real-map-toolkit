// HarbinBridgeLift —— Mod 入口
//
// 全部工作都在设置面板的按钮回调里同步完成（跟 Earth2Cities 的
// Move Imported Roads / Undo Import 一样），所以不需要注册任何 System。

using System;
using System.IO;
using Colossal;
using Colossal.IO.AssetDatabase;
using Colossal.Logging;
using Game;
using Game.Modding;
using Game.SceneFlow;

namespace HarbinBridgeLift
{
    public class Mod : IMod
    {
        public static readonly ILog log =
            LogManager.GetLogger("HarbinBridgeLift").SetShowsErrorsInUI(false);

        public static Setting Settings { get; private set; }

        /// <summary>Mod 安装目录（= ...\Cities Skylines II\Mods\HarbinBridgeLift）</summary>
        public static string InstallFolder { get; private set; }

        private Setting m_Setting;

        public void OnLoad(UpdateSystem updateSystem)
        {
            log.Info("[HarbinBridgeLift] OnLoad");

            try
            {
                ExecutableAsset asset = default;
                if (GameManager.instance.modManager.TryGetExecutableAsset(this, out asset))
                {
                    InstallFolder = Path.GetDirectoryName(asset.path);
                    log.Info("Mod install folder: " + InstallFolder);
                }
            }
            catch (Exception ex)
            {
                log.Warn("Failed to get mod asset path: " + ex.Message);
            }

            if (string.IsNullOrEmpty(InstallFolder))
            {
                InstallFolder = Path.GetFullPath(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "..", "LocalLow", "Colossal Order", "Cities Skylines II",
                    "Mods", "HarbinBridgeLift"));
                log.Info("Fallback mod folder: " + InstallFolder);
            }

            m_Setting = new Setting(this);
            Settings = m_Setting;
            m_Setting.RegisterInOptionsUI();
            GameManager.instance.localizationManager.AddSource("en-US", new Locale(m_Setting, false));
            GameManager.instance.localizationManager.AddSource("zh-HANS", new Locale(m_Setting, true));
            AssetDatabase.global.LoadSettings("HarbinBridgeLift", m_Setting, new Setting(this), false);

            // 所有 ECS 读写都在这个相位里做 —— UI 回调里做结构变更会原生崩溃（v1 实测）
            updateSystem.UpdateAt<BridgeLiftSystem>(SystemUpdatePhase.ModificationEnd);
            log.Info("[HarbinBridgeLift] BridgeLiftSystem registered at ModificationEnd");

            // 铁路铺设工具：必须注册进更新循环，否则 OnUpdate 永远不被调用 →
            // applyMode 永远停在 None → 原版 ApplyNetSystem 不会把定义实体变成真网络
            // （2026-09-29 两次试建都是 0 边的头号嫌疑；E2C 也是 UpdateAt<..>(ToolUpdate)）
            updateSystem.UpdateAt<RailLayToolSystem>(SystemUpdatePhase.ToolUpdate);
            log.Info("[HarbinBridgeLift] RailLayToolSystem registered at ToolUpdate");

            // 车站放置工具（S2 车站轮）：与 RailLayToolSystem 同构的对象分支（ObjectDefinition）。
            // 必须同样注册进更新循环，否则 OnUpdate 不被调用 → applyMode 停在 None → 对象不落地。
            updateSystem.UpdateAt<StationPlaceToolSystem>(SystemUpdatePhase.ToolUpdate);
            log.Info("[HarbinBridgeLift] StationPlaceToolSystem registered at ToolUpdate");

            log.Info("[HarbinBridgeLift] loaded; data file should be at " + BridgeLiftData.DefaultPath);
        }

        public void OnDispose()
        {
            log.Info("[HarbinBridgeLift] OnDispose");
            if (m_Setting != null)
            {
                m_Setting.UnregisterInOptionsUI();
                m_Setting = null;
                Settings = null;
            }
        }
    }
}
