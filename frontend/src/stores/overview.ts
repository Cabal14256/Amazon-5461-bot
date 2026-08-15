import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getOverview } from '@/api'
import type { OverviewData } from '@/types'

export const useOverviewStore = defineStore('overview', () => {
  const data = ref<OverviewData | null>(null)
  const loading = ref(false)

  async function refresh() {
    loading.value = true
    try {
      data.value = await getOverview()
    } finally {
      loading.value = false
    }
  }

  return { data, loading, refresh }
})
