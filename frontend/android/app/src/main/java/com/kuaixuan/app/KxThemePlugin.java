package com.kuaixuan.app;

import android.view.Window;

import androidx.core.content.ContextCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * 快选安卓壳：把 **App 自己的主题**同步给系统栏（2026-10-05，APK 1.8.3）。
 *
 * 🔴 为什么要这个插件（主人反馈：「信号那一栏现在是黑的，字和信号都看不见」＋
 *    「不应该是 app 和系统颜色联动吗」）：
 *    此前系统栏那条带子的颜色写在 `values/` 与 `values-night/` 里，由 **DayNight 资源**
 *    决定 ⇒ 它跟着**系统的**深浅色走。而 App 自己的主题是网页里单独存的
 *    （账号 prefs / localStorage），两个独立源头 ⇒ 系统深色 + App 浅色（红顶栏）时：
 *    上面一条黑、下面一条红；黑底上若再叠深色图标，时间/信号就完全看不见。
 *
 * 修法：**以 App 主题为唯一源头**。网页每次应用主题（启动 + 手动切换）都调用本插件的
 * `apply({ mode })`，这里一次性改写：
 *   ① 窗口底（他那台机器 Android 15 上，系统栏区域露的正是 windowBackground）
 *   ② 状态栏底（在仍尊重 statusBarColor 的机型/系统上生效）
 *   ③ 导航栏底
 *   ④ 状态栏图标恒为**亮色**（红底与深底都偏暗，白图标在两种主题下都看得清）
 *   ⑤ 导航栏图标随底栏明暗取反
 *
 * ⇒ 从此「系统 → App 主题 → 系统栏」单向联动：App 浅色=红条，深色=深条，图标始终可见。
 */
@CapacitorPlugin(name = "KxTheme")
public class KxThemePlugin extends Plugin {

    @PluginMethod
    public void apply(PluginCall call) {
        String mode = call.getString("mode");
        final boolean dark = !"light".equals(mode);

        getActivity().runOnUiThread(() -> {
            try {
                Window w = getActivity().getWindow();
                w.setBackgroundDrawableResource(
                        dark ? R.drawable.window_bg_dark : R.drawable.window_bg_light);
                w.setStatusBarColor(ContextCompat.getColor(getContext(),
                        dark ? R.color.kxStatusBarDark : R.color.kxStatusBarLight));
                w.setNavigationBarColor(ContextCompat.getColor(getContext(),
                        dark ? R.color.kxNavBarDark : R.color.kxNavBarLight));

                WindowInsetsControllerCompat ic =
                        WindowCompat.getInsetsController(w, w.getDecorView());
                if (ic != null) {
                    // 红底(#c62828)与深底(#0f1219)都偏暗 ⇒ 图标恒白，两种主题下都可见
                    ic.setAppearanceLightStatusBars(false);
                    // 底栏：浅色底配深图标，深色底配亮图标
                    ic.setAppearanceLightNavigationBars(!dark);
                }
            } catch (Throwable ignored) {
                // 主题同步失败不能影响页面：静默吞掉
            }
            call.resolve();
        });
    }
}
