import type { GlobalThemeOverrides } from 'naive-ui'

/**
 * 浅色为主主题；深色通过 darkTheme + 同一份 overrides 切换（见 stores/theme.ts）。
 * 卡片圆角 10px，统一全站圆角语言。
 */
export const themeOverrides: GlobalThemeOverrides = {
  common: {
    borderRadius: '6px',
    primaryColor: '#2080f0',
    primaryColorHover: '#4098fc',
    primaryColorPressed: '#1060c9',
    fontFamilyMono: 'ui-monospace, SFMono-Regular, "Cascadia Mono", Consolas, monospace',
  },
  Card: {
    borderRadius: '10px',
  },
  Button: {
    borderRadiusMedium: '8px',
  },
  Tag: {
    borderRadius: '6px',
  },
}
