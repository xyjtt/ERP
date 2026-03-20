import { PlatformKey } from "./types";

export const platformLabels: Record<PlatformKey, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  pinduoduo: "拼多多",
  taobao: "淘宝",
  tmall: "天猫",
  xiaohongshu: "小红书",
};

export const selectors = {
  login: {
    username: [
      'input[placeholder*="账号"]',
      'input[placeholder*="用户名"]',
      'input[name="username"]',
      'input[type="text"]',
    ],
    password: ['input[placeholder*="密码"]', 'input[name="password"]', 'input[type="password"]'],
    agreementModalConfirm: [
      '.ant-modal button:has-text("确定")',
      'button:has-text("确定")',
      'text=确定',
    ],
    agreementCheckbox: [
      'label:has-text("我已阅读并同意")',
      'text=我已阅读并同意',
      'input[type="checkbox"]',
    ],
    submit: ['button:has-text("登录")', 'text=登录'],
  },
  productPage: {
    guideClose: ['button:has-text("知道了")', 'text=知道了'],
    entryLinks: [
      'text=店铺商品管理',
      'text=商品管理',
      '[role="menuitem"]:has-text("店铺商品管理")',
      '[role="menuitem"]:has-text("商品管理")',
      '.ant-menu-item:has-text("店铺商品管理")',
      '.ant-menu-item:has-text("商品管理")',
      'a:has-text("店铺商品管理")',
      'a:has-text("商品管理")',
    ],
    queryForm: ['#shopGoodsImportQuery'],
    skuTab: ['role=tab[name="按SKU"]', 'role=tab[name="按sku"]', 'text=按SKU', 'text=按sku'],
    multiSkuInput: [
      '#shopGoodsImportQuery_itemCode input.ant-input',
      'xpath=//*[@id="shopGoodsImportQuery_itemCode"]/span/div[2]/span/span/span[1]/input',
      'input[placeholder*="多个线上商品编码，以逗号分隔"]',
      'input[placeholder*="多个线上商品编码"]',
      'textarea[placeholder*="多个线上商品编码"]',
    ],
    platformTrigger: [
      'input#shopGoodsImportQuery_shopIds',
      'xpath=//*[@id="shopGoodsImportQuery_shopIds"]/ancestor::span[contains(@class,"ant-input-affix-wrapper")][1]',
      'xpath=//*[@id="shopGoodsImportQuery_shopIds"]/ancestor::div[contains(@class,"ant-form-item-control-input-content")][1]',
      'input[placeholder*="请选择平台/店铺"]',
      'input[readonly][id="shopGoodsImportQuery_shopIds"]',
    ],
    platformModal: ['.ant-modal-wrap', '.ant-modal'],
    platformModalSearchInput: ['.ant-modal-wrap input.ant-input', '.ant-modal input.ant-input'],
    platformModalSearchButton: [
      '.ant-modal-wrap button:has-text("搜索")',
      '.ant-modal button:has-text("搜索")',
      '.ant-modal-wrap button.ant-btn-primary',
      '.ant-modal button.ant-btn-primary',
    ],
    platformModalResetButton: [
      '.ant-modal-wrap button:has-text("重置")',
      '.ant-modal button:has-text("重置")',
    ],
    platformModalFooter: ['.ant-modal-wrap .ant-modal-footer', '.ant-modal .ant-modal-footer'],
    platformModalFooterSelectAll: [
      '.ant-modal-wrap .ant-modal-footer label.ant-checkbox-wrapper',
      '.ant-modal .ant-modal-footer label.ant-checkbox-wrapper',
      '.ant-modal-wrap .ant-modal-footer .ant-checkbox-wrapper',
      '.ant-modal .ant-modal-footer .ant-checkbox-wrapper',
    ],
    platformModalFooterSelectAllInput: [
      '.ant-modal-wrap .ant-modal-footer input.ant-checkbox-input',
      '.ant-modal .ant-modal-footer input.ant-checkbox-input',
    ],
    platformModalRowCheckboxInputs: [
      'tbody tr input.ant-checkbox-input',
      '.ant-table-tbody tr input.ant-checkbox-input',
      '.ant-tree-list-holder input.ant-checkbox-input',
      '.ant-tree-treenode input.ant-checkbox-input',
    ],
    platformCheckboxLabels: ['label.ant-checkbox-wrapper'],
    platformModalConfirm: [
      '.ant-modal-wrap .ant-modal-footer button.ant-btn-primary',
      '.ant-modal-footer button.ant-btn-primary',
      '.ant-modal button:has-text("确定")',
    ],
    quickSaveModal: ['.ant-modal:has-text("另存为快捷查询")', '.ant-modal-wrap:has-text("另存为快捷查询")'],
    queryButton: [
      'xpath=//*[@id="shopGoodsImportQuery"]/div[2]/div/div[2]/div/div[2]/div[2]/button',
      '#shopGoodsImportQuery button.ant-btn.ant-btn-primary',
      '#shopGoodsImportQuery button:has-text("搜索")',
    ],
    batchUpdateButton: [
      'button.ant-btn:has(span:text-is("批量更新线上商品编码"))',
      'button:has(span:text-is("批量更新线上商品编码"))',
      'button:has-text("批量更新线上商品编码")',
      'text=批量更新线上商品编码',
    ],
    updateModal: [
      '.ant-modal-wrap:has(input#title_name8)',
      '.ant-modal:has(input#title_name8)',
      '.ant-modal-wrap:has-text("批量填充")',
      '.ant-modal:has-text("批量填充")',
    ],
    fillModeButton: ['label.ant-radio-wrapper:has-text("批量填充")', 'text=批量填充'],
    updateInput: [
      '.ant-modal input#title_name8',
      '.ant-modal-wrap input#title_name8',
      '.ant-modal input[placeholder*="批量填充商品编码"]',
      '.ant-modal-wrap input[placeholder*="批量填充商品编码"]',
    ],
    confirmButton: [
      '.ant-modal-wrap .ant-modal-footer button.ant-btn.ant-btn-primary',
      '.ant-modal-footer button.ant-btn.ant-btn-primary',
      '.ant-modal button:has-text("确定")',
      'button:has-text("确定")',
    ],
    popupMessage: ['.ant-message-notice-content', '.ant-modal-body', '.el-message', '[role="alert"]'],
    tableRows: ['table tbody tr', '.ant-table-tbody > tr'],
    pageSizeTrigger: [
      'text=/\\d+条\\/页$/',
      '.ant-pagination-options .ant-select-selector',
      '.ant-pagination-options .ant-select-selection-item',
    ],
    pageSizeOption500: [
      'text=/^500条\\/页$/',
      'text=/^500\\s*条\\/页$/',
      '.ant-select-item-option[title="500条/页"]',
      '.ant-select-item-option:has-text("500条/页")',
    ],
  },
} as const;
