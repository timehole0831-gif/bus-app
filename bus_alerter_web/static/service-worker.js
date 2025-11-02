// static/service-worker.js

// 서비스 워커가 푸시 이벤트를 받았을 때 실행될 코드
self.addEventListener('push', function(event) {
  // 푸시 메시지로 받은 데이터를 파싱
  const data = event.data.json();

  const title = data.title || '버스 알리미';
  const options = {
    body: data.body,
    icon: 'https://i.imgur.com/O9CLv2S.png', // 알림 아이콘
    badge: 'https://i.imgur.com/O9CLv2S.png' // 작은 아이콘 (안드로이드)
  };

  // 브라우저에 알림을 표시하도록 명령
  event.waitUntil(self.registration.showNotification(title, options));
});