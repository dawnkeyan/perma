export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/journal/index',
    'pages/chat/index',
    'pages/timeline/index',
    'pages/profile/index',
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#F7F5F2',
    navigationBarTitleText: '拾光',
    navigationBarTextStyle: 'black',
  },
  tabBar: {
    color: '#9A958E',
    selectedColor: '#C87F5F',
    backgroundColor: '#FFFFFF',
    borderStyle: 'white',
    list: [
      { pagePath: 'pages/index/index', text: '今天' },
      { pagePath: 'pages/journal/index', text: '记录' },
      { pagePath: 'pages/chat/index', text: '对话' },
      { pagePath: 'pages/timeline/index', text: '回顾' },
    ],
  },
});
