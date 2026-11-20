<script setup>
// 从统一入口主动发起一条新消息（不是回复某条已有消息）。
// 抽成组件的原因：这段逻辑有 4 个字段、3 个校验、1 次投递，
// 混在 App.vue 的 1200 行 script 里没人敢动。
import { computed, reactive, ref, watch } from 'vue'
import { message as antMessage } from 'ant-design-vue'
import * as api from '../api'
import { PLATFORM_LABEL } from '../api'

const props = defineProps({
  open: { type: Boolean, default: false },
  // 在线平台（只列已接入的，避免选了发不出去）
  platforms: { type: Array, default: () => [] },
  // 已有会话生成的联系人候选，格式 [{ value, label }]
  contacts: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:open', 'sent'])

const sending = ref(false)

const blank = () => ({ platform: undefined, target: '', subject: '', text: '' })
const form = reactive(blank())

// 每次打开都从默认平台起手，避免上次填了一半的邮箱残留在飞书会话里
watch(
  () => props.open,
  (v) => {
    if (v) {
      Object.assign(form, blank())
      form.platform = (props.platforms.find((p) => p.ready) || {}).platform
    }
  },
)

const targetOptions = computed(() =>
  props.contacts.filter((c) => !form.platform || c.platform === form.platform),
)

const placeholder = computed(() =>
  form.platform === 'email' ? 'someone@example.com' : '从已有会话里选，或直接粘 chat_id',
)

function close() {
  emit('update:open', false)
}

async function submit() {
  const target = (form.target || '').trim()
  const text = (form.text || '').trim()
  if (!target || !text) {
    antMessage.warning('联系人和内容都要填')
    return
  }
  if (form.platform === 'email' && !target.includes('@')) {
    antMessage.warning('邮箱收件人要写完整地址')
    return
  }
  sending.value = true
  try {
    await api.sendNew({
      platform: form.platform,
      target,
      text,
      subject: (form.subject || '').trim() || null,
    })
    antMessage.success('已投递，稍等几秒对方就能收到')
    close()
    emit('sent')
  } catch (e) {
    antMessage.error('发送失败：' + (e.message || e), 8)
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="发起新消息"
    :confirm-loading="sending"
    ok-text="发送"
    cancel-text="取消"
    @ok="submit"
    @cancel="close"
  >
    <div class="nm-form">
      <div class="nm-row">
        <label>平台</label>
        <a-select v-model:value="form.platform" size="small" style="width: 100%">
          <a-select-option
            v-for="p in platforms"
            :key="p.platform"
            :value="p.platform"
            :disabled="!p.ready"
          >
            {{ PLATFORM_LABEL[p.platform] || p.platform }}{{ p.ready ? '' : '（未接入）' }}
          </a-select-option>
        </a-select>
      </div>

      <div class="nm-row">
        <label>{{ form.platform === 'email' ? '收件人' : '联系人' }}</label>
        <a-auto-complete
          v-model:value="form.target"
          :options="targetOptions"
          size="small"
          style="width: 100%"
          :placeholder="placeholder"
        />
      </div>

      <div v-if="form.platform === 'email'" class="nm-row">
        <label>主题</label>
        <a-input v-model:value="form.subject" size="small" placeholder="留空自动取正文首行" />
      </div>

      <div class="nm-row">
        <label>内容</label>
        <a-textarea v-model:value="form.text" :rows="5" placeholder="要发出去的话…" />
      </div>

      <div class="nm-tip">发出后走 Outbox 投递，失败会自动重试；成功后这条会出现在收件箱里。</div>
    </div>
  </a-modal>
</template>

<style scoped>
.nm-form { display: flex; flex-direction: column; gap: 12px; }
.nm-row { display: flex; flex-direction: column; gap: 5px; }
.nm-row > label { font-size: 12px; color: var(--t2); }
.nm-tip { font-size: 12px; color: var(--t3); line-height: 1.6; }
</style>
