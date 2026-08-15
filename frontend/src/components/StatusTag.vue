<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import { getStatusMeta, type StatusKind } from '@/theme/statusColors'

const props = defineProps<{
  kind: StatusKind
  status: string
  size?: 'small' | 'medium' | 'large'
}>()

const meta = computed(() => getStatusMeta(props.kind, props.status))
</script>

<template>
  <n-tag
    :size="size ?? 'medium'"
    :bordered="false"
    class="status-tag"
    :style="{ color: meta.color, backgroundColor: meta.color + '1a' }"
  >
    <template #icon>
      <span
        class="status-dot"
        :class="{ pulse: meta.pulse, dashed: meta.dashed }"
        :style="{ '--dot-color': meta.color }"
      />
    </template>
    {{ meta.label }}
  </n-tag>
</template>

<style scoped>
.status-tag {
  font-weight: 500;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--dot-color);
  display: inline-block;
}

.status-dot.dashed {
  background-color: transparent;
  border: 1.5px dashed var(--dot-color);
}

.status-dot.pulse {
  animation: status-pulse 1.6s ease-in-out infinite;
}

@keyframes status-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--dot-color) 45%, transparent);
  }
  50% {
    box-shadow: 0 0 0 5px transparent;
  }
}
</style>
