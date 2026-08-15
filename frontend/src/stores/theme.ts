import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { darkTheme, type GlobalTheme } from 'naive-ui'

const STORAGE_KEY = 'console-theme'

/** 浅色为主，深色一键切换并持久化到 localStorage */
export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(localStorage.getItem(STORAGE_KEY) === 'dark')

  const theme = computed<GlobalTheme | null>(() => (isDark.value ? darkTheme : null))

  function toggle() {
    isDark.value = !isDark.value
  }

  watch(isDark, (v) => {
    localStorage.setItem(STORAGE_KEY, v ? 'dark' : 'light')
    document.documentElement.classList.toggle('dark', v)
  }, { immediate: true })

  return { isDark, theme, toggle }
})
