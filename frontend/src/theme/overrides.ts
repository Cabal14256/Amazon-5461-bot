import type { GlobalThemeOverrides } from 'naive-ui'

/** 浅色主主题；深色用 naive-ui darkTheme + 同一套 overrides */
export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#2080f0',
    primaryColorHover: '#4098fc',
    primaryColorPressed: '#1060c9',
    borderRadius: '6px',
    fontFamilyMono: 'ui-monospace, "Cascadia Mono", Consolas, monospace',
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
