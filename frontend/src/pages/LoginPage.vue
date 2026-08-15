<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NForm, NFormItem, NInput } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/http'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref<string | null>(null)

async function onSubmit() {
  if (!username.value.trim() || !password.value || loading.value) return
  loading.value = true
  error.value = null
  try {
    await auth.login(username.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') ? redirect : '/')
  } catch (e) {
    if (e instanceof ApiError && e.status === 429) {
      error.value = '尝试次数过多，已临时锁定，请稍后再试'
    } else if (e instanceof ApiError && e.status === 401) {
      error.value = '用户名或密码错误'
    } else {
      error.value = '登录失败，请确认后端控制台服务已启动'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-shell">
    <n-card class="login-card">
      <div class="login-brand">
        <span class="brand-mark">5461</span>
        <span class="brand-name">内网控制台</span>
      </div>
      <p class="login-subtitle">只读控制台 · 请使用本地账号登录</p>

      <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 16px">
        {{ error }}
      </n-alert>

      <n-form @submit.prevent="onSubmit">
        <n-form-item label="用户名">
          <n-input
            v-model:value="username"
            placeholder="用户名"
            autocomplete="username"
            autofocus
            @keyup.enter="onSubmit"
          />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            placeholder="密码"
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
        </n-form-item>
        <n-button
          type="primary"
          block
          :loading="loading"
          :disabled="!username.trim() || !password"
          attr-type="submit"
          @click="onSubmit"
        >
          登录
        </n-button>
      </n-form>
    </n-card>
  </div>
</template>

<style scoped>
.login-shell {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.login-card {
  width: 380px;
  max-width: 100%;
}

.login-brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  justify-content: center;
}

.brand-mark {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-weight: 800;
  font-size: 24px;
  color: #2080f0;
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  opacity: 0.85;
}

.login-subtitle {
  text-align: center;
  font-size: 13px;
  opacity: 0.55;
  margin: 8px 0 20px;
}
</style>
