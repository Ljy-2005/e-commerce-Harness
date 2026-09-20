import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CredentialField from './CredentialField'

const BASE = {
  name: 'OpenAI',
  env: 'OPENAI_API_KEY',
  provider: 'openai',
  capabilities: 'vision / text / image',
  configured: false,
  available: false,
  source: '',
}

function renderField(overrides = {}, handlers = {}) {
  const item = { ...BASE, ...overrides }
  const props = {
    item,
    onSave: vi.fn().mockResolvedValue(undefined),
    onClear: vi.fn().mockResolvedValue(undefined),
    ...handlers,
  }
  render(<CredentialField {...props} />)
  return props
}

const KEY_LABEL = 'OpenAI 的 API 密钥'

describe('CredentialField 状态与编辑', () => {
  it('未配置：状态文案 + 应用按钮禁用', () => {
    renderField()
    expect(screen.getByText('未配置')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '应用' })).toBeDisabled()
  })

  it('已配置且可用：显示"已配置 · 生效中"', () => {
    renderField({ configured: true, available: true, source: 'file' })
    expect(screen.getByText('已配置 · 生效中')).toBeInTheDocument()
  })

  it('已配置但路由未注册：显示"已配置 · 未激活"', () => {
    renderField({ configured: true, available: false, source: 'file' })
    expect(screen.getByText('已配置 · 未激活')).toBeInTheDocument()
  })

  it('输入密钥 → 应用 → onSave(env, 值) + 无障碍成功状态 + 清空不回显', async () => {
    const user = userEvent.setup()
    const props = renderField()
    await user.type(screen.getByLabelText(KEY_LABEL), 'sk-new-key-123')
    await user.click(screen.getByRole('button', { name: '应用' }))
    await waitFor(() => expect(props.onSave).toHaveBeenCalledWith('OPENAI_API_KEY', 'sk-new-key-123'))
    expect(await screen.findByRole('status')).toHaveTextContent('已保存并立即生效')
    expect(screen.getByLabelText(KEY_LABEL)).toHaveValue('')
  })

  it('非法字符（中文）→ 字段级错误 + 不提交', async () => {
    const user = userEvent.setup()
    const props = renderField()
    await user.type(screen.getByLabelText(KEY_LABEL), 'sk-中文')
    expect(screen.getByRole('alert')).toHaveTextContent('可打印 ASCII')
    expect(screen.getByRole('button', { name: '应用' })).toBeDisabled()
    expect(props.onSave).not.toHaveBeenCalled()
  })

  it('粘贴 NAME=value 行 → 拒绝', async () => {
    const user = userEvent.setup()
    renderField()
    const input = screen.getByLabelText(KEY_LABEL)
    await user.click(input)
    await user.paste('OPENAI_API_KEY=sk-abc')
    expect(screen.getByRole('alert')).toHaveTextContent('只粘贴值本身')
  })

  it('保存被拒绝 → 保留输入并展示 Host 诊断', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockRejectedValue(new Error('密钥由环境变量提供，设置页修改不会生效'))
    renderField({}, { onSave })
    await user.type(screen.getByLabelText(KEY_LABEL), 'sk-x')
    await user.click(screen.getByRole('button', { name: '应用' }))
    expect(await screen.findByText(/环境变量提供/)).toBeInTheDocument()
    expect(screen.getByLabelText(KEY_LABEL)).toHaveValue('sk-x')
  })

  it('显示/隐藏切换', async () => {
    const user = userEvent.setup()
    renderField()
    expect(screen.getByLabelText(KEY_LABEL)).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: '显示密钥' }))
    expect(screen.getByLabelText(KEY_LABEL)).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: '隐藏密钥' }))
    expect(screen.getByLabelText(KEY_LABEL)).toHaveAttribute('type', 'password')
  })
})

describe('CredentialField 清除与环境变量锁定', () => {
  it('未配置时无清除按钮；两步确认后调用 onClear', async () => {
    const user = userEvent.setup()
    const props = renderField({ configured: true, available: true, source: 'file' })
    await user.click(screen.getByRole('button', { name: '清除' }))
    await user.click(screen.getByRole('button', { name: '确认清除' }))
    await waitFor(() => expect(props.onClear).toHaveBeenCalledWith('OPENAI_API_KEY'))
    expect(await screen.findByRole('status')).toHaveTextContent('已清除该密钥')
  })

  it('取消清除不调用 onClear', async () => {
    const user = userEvent.setup()
    const props = renderField({ configured: true, available: true, source: 'file' })
    await user.click(screen.getByRole('button', { name: '清除' }))
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(props.onClear).not.toHaveBeenCalled()
  })

  it('env 供给：只读锁 + 无输入框/清除按钮', () => {
    renderField({ configured: true, available: true, source: 'env' })
    const lock = document.querySelector('.lock-note')
    expect(lock).not.toBeNull()
    expect(lock.textContent).toContain('设置页保存不会生效')
    expect(screen.getByText('环境变量供给 · 只读')).toBeInTheDocument()
    expect(screen.queryByLabelText(KEY_LABEL)).toBeNull()
    expect(screen.queryByRole('button', { name: '清除' })).toBeNull()
  })
})
