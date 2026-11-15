import { GlobalOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useTranslation } from 'react-i18next'
import { setUiLanguage, uiLanguage } from '../i18n'

/**
 * Interface language toggle. The label names the language to switch to ("English" in the Chinese
 * interface, "中文" in the English one), as in the Ant Design site header. Independent of the template
 * field-name language.
 */
export function LanguageSwitch() {
  const { i18n } = useTranslation()
  const english = i18n.language === 'en'
  return (
    <Button
      type="text"
      icon={<GlobalOutlined />}
      aria-label={english ? 'Switch to Chinese' : 'Switch to English'} // i18n-ignore: fixed per target
      onClick={() => setUiLanguage(uiLanguage() === 'en' ? 'zh-CN' : 'en')}
    >
      {/* i18n-ignore-next-line: the label names the target language */}
      {english ? '中文' : 'English'}
    </Button>
  )
}
