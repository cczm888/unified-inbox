import { createApp } from 'vue'
import 'ant-design-vue/dist/reset.css'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import App from './App.vue'
import './style.css'

// 界面全是中文，dayjs 的「周几 / 相对时间」也得是中文
dayjs.locale('zh-cn')

// 不再 use(Antd)：组件由 vite.config.js 里的 AntDesignVueResolver 按需引入，
// 模板里照常写 <a-button> / <a-modal> 即可，构建时只会打进用到的那十几个组件。
createApp(App).mount('#app')
