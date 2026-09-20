import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Modal from './Modal'

describe('Modal（通用悬浮弹窗）', () => {
  it('role=dialog + aria-modal，标题可读', () => {
    render(<Modal title="新建风格词条" onClose={() => {}}><p>内容</p></Modal>)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('新建风格词条')
    expect(screen.getByText('内容')).toBeInTheDocument()
  })

  it('点遮罩关闭（点卡片内部不关闭）', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<Modal title="t" onClose={onClose}><p>内容</p></Modal>)

    await user.click(screen.getByText('内容'))
    expect(onClose).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('modal-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ESC 关闭', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<Modal title="t" onClose={onClose}><p>内容</p></Modal>)

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('关闭按钮可点击（带可访问名称）', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<Modal title="t" onClose={onClose}><p>内容</p></Modal>)

    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('打开时锁背景滚动，卸载后还原', () => {
    const { unmount } = render(<Modal title="t" onClose={() => {}}><p>内容</p></Modal>)
    expect(document.body.style.overflow).toBe('hidden')
    unmount()
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('渲染 footer（底部操作区）', () => {
    render(<Modal title="t" onClose={() => {}} footer={<button>开始分析</button>}><p>内容</p></Modal>)
    expect(screen.getByRole('button', { name: '开始分析' })).toBeInTheDocument()
  })
})
