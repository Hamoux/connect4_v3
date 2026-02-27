////////////////////////////////////////////////////////////////////
//
// BGA service worker
//
// Must be enabled to control the whole domain in order to be able to manage page navigation.
//
// This means:
// - that the file must be served with Header set Service-Worker-Allowed "/"
// - that the service worker must be registered with the appropriate scope parameter to control "/"
//
////////////////////////////////////////////////////////////////////


//////////////////////////////////
// Analytics helper code from https://developers.google.com/web/ilt/pwa/integrating-analytics#analytics_and_service_worker

// Set this to your tracking ID (mindlab or boardgamearena)
var trackingId = self.location.hostname.indexOf('mindlab') >= 0 || self.location.hostname.indexOf('mindzup') >= 0 ? 'UA-46865025-2' : 'UA-46865025-1';

function sendAnalyticsEvent(eventAction, eventCategory) {
  'use strict';

  console.log('Sending analytics event: ' + eventCategory + '/' + eventAction);

  if (!trackingId) {
    console.error('You need your tracking ID in analytics-helper.js');
    console.error('Add this code:\nvar trackingId = \'UA-XXXXXXXX-X\';');
    // We want this to be a safe method, so avoid throwing unless absolutely necessary.
    return Promise.resolve();
  }

  if (!eventAction && !eventCategory) {
    console.warn('sendAnalyticsEvent() called with no eventAction or ' +
    'eventCategory.');
    // We want this to be a safe method, so avoid throwing unless absolutely necessary.
    return Promise.resolve();
  }

  return self.registration.pushManager.getSubscription()
  .then(function(subscription) {
    if (subscription === null) {
      throw new Error('No subscription currently available.');
    }

    // Create hit data
    var payloadData = {
      // Version Number
      v: 1,
      // Client ID
      cid: subscription.endpoint,
      // Tracking ID
      tid: trackingId,
      // Hit Type
      t: 'event',
      // Event Category
      ec: eventCategory,
      // Event Action
      ea: eventAction,
      // Event Label
      el: 'serviceworker'
    };

    // Format hit data into URI
    var payloadString = Object.keys(payloadData)
    .filter(function(analyticsKey) {
      return payloadData[analyticsKey];
    })
    .map(function(analyticsKey) {
      return analyticsKey + '=' + encodeURIComponent(payloadData[analyticsKey]);
    })
    .join('&');

    // Post to Google Analytics endpoint
    return fetch('https://www.google-analytics.com/collect', {
      method: 'post',
      body: payloadString
    });
  })
  .then(function(response) {
    if (!response.ok) {
      return response.text()
      .then(function(responseText) {
        throw new Error(
          'Bad response from Google Analytics:\n' + response.status
        );
      });
    } else {
      console.log(eventCategory + '/' + eventAction +
        'hit sent, check the Analytics dashboard');
    }
  })
  .catch(function(err) {
    console.warn('Unable to send the analytics event', err);
  });
}

//////////////////////////////////
// Handle push events

self.addEventListener('push', function(event) {
    console.log('## WebPushServiceWorker v20190828-1253: push event', event);

    try {
      if (event.data !== null) {
        console.log('## WebPushServiceWorker: push event / data', event.data.text());
        
        const myData = event.data.json();

        const analyticsPromise = self.sendAnalyticsEvent('push', myData.options.data.type);
        let notificationPromise = Promise.resolve();

        if (myData.options.data.type != 'notification-yourturn-realtime'
                && myData.options.data.type != 'notification-yourturn-turnbased'
                && myData.options.data.type != 'notification-table-end') {
            // Show notification
            console.log('## WebPushServiceWorker: push event / show ' + myData.options.data.type);
            notificationPromise = self.registration.showNotification(myData.title, myData.options);
        } else {
            // For 'yourturn' and 'table-end' notifications, we do not show the notification if the page is already visible
            const url = myData.options.data.url;
            const urlToOpen = new URL(url, self.location.origin).href;

            console.log('## WebPushServiceWorker: push event / url to open', urlToOpen );
            
            const tableToOpen = (urlToOpen.indexOf('?table=') >= 0 ? urlToOpen.substr(urlToOpen.indexOf('?table=') + 7) : null);
            
            console.log('## WebPushServiceWorker: push event / table to open', tableToOpen );

            notificationPromise = clients.matchAll({
                type: 'window',
                includeUncontrolled: true
            })
            .then((windowClients) => {
                let matchingClient = null;
                
                for (let i = 0; i < windowClients.length; i++) {
                    const windowClient = windowClients[i];
                    if (windowClient.url === 'about:blank') continue;

                    console.log('## WebPushServiceWorker: push event / controlled page', windowClient.url);
                    
                    if (windowClient.url.indexOf('blank?gsgameurl=') >= 0) {
                        // This is a gameserver in-game iframe pointing to the mainsite to give us access to the page from the mainsite domain
                        const gameurl = windowClient.url.substr(windowClient.url.indexOf('=') + 1);
                        const clientTable = gameurl.substr(gameurl.indexOf('?table=') + 7);
                        console.log('## WebPushServiceWorker: push event / client table', clientTable );
                        if (urlToOpen.indexOf(gameurl) >= 0 || (urlToOpen.indexOf('/play?table=')  >= 0 || urlToOpen.indexOf('table?table=') >= 0) && clientTable === tableToOpen) {
                            // It's a match on the gameserver
                            console.log('## WebPushServiceWorker: push event / game page found with visibility:', windowClient.visibilityState  );
                            matchingClient = windowClient;
                            break;
                        }
                    }
                }

                if (matchingClient !== null && 'focus' in matchingClient && matchingClient.visibilityState == 'visible') {
                    // This page is visible already, do not show notification
                    console.log('## WebPushServiceWorker: push event / game page is visible, do not show ' + myData.options.data.type);
                    return new Promise(function () {}); // We return a never fulfilled promise to prevent a forced notification of the browser https://stackoverflow.com/questions/33092065/google-chrome-silent-push-notifications
                } else {
                    // Show notification
                    console.log('## WebPushServiceWorker: push event / game page is not visible, show ' + myData.options.data.type);
                    return self.registration.showNotification(myData.title, myData.options);
                }
            });
        }

        const promiseChain = Promise.all([
            analyticsPromise,
            notificationPromise
        ]);

        event.waitUntil(promiseChain);
      } else {
        // Should never happen
      }
    } catch (error) {
        console.error( '## WebPushServiceWorker error', error );
    }
});

//////////////////////////////////
// Handle notificationclick events

self.addEventListener('notificationclick', function(event) {
    console.log('## WebPushServiceWorker: notificationclick event', event);

    const clickedNotification = event.notification;
    const url = clickedNotification.data.url;

    // Close notification
    clickedNotification.close();

    const analyticsPromise = self.sendAnalyticsEvent('click', clickedNotification.data.type);
    let actionPromise = Promise.resolve();

    if (typeof url != 'undefined') {
        // Open url or focus the tab if this url is already opened
        const urlToOpen = new URL(url, self.location.origin).href;

        console.log('## WebPushServiceWorker: notificationclick event / url to open', urlToOpen);

        const tableToOpen = (urlToOpen.indexOf('?table=') >= 0 ? urlToOpen.substr(urlToOpen.indexOf('?table=') + 7) : null);
            
        console.log('## WebPushServiceWorker: notificationclick event / table to open', tableToOpen );

        actionPromise = clients.matchAll({
            type: 'window',
            includeUncontrolled: true
        })
        .then((windowClients) => {
            let matchingClient = null;
            let isMainsiteMatch = true;
            let firstMainsiteClient = null;
            
            for (let i = 0; i < windowClients.length; i++) {
                const windowClient = windowClients[i];
                if (windowClient.url === 'about:blank') continue;
                
                console.log('## WebPushServiceWorker: notificationclick event / controlled page', windowClient.url);
                
                if (windowClient.url.indexOf('blank?gsgameurl=') >= 0) {
                    // This is a gameserver in-game iframe pointing to the mainsite to give us access to the page from the mainsite domain
                    const gameurl = windowClient.url.substr(windowClient.url.indexOf('=') + 1);
                    const clientTable = gameurl.substr(gameurl.indexOf('?table=') + 7);
                    console.log('## WebPushServiceWorker: notificationclick event / client table', clientTable );
                    if (urlToOpen.indexOf(gameurl) >= 0 || (urlToOpen.indexOf('/play?table=')  >= 0 || urlToOpen.indexOf('table?table=') >= 0) && clientTable === tableToOpen) {
                        // It's a match on the gameserver
                        console.log('## WebPushServiceWorker: notificationclick event / game page found with visibility:', windowClient.visibilityState  );
                        matchingClient = windowClient;
                        isMainsiteMatch = false;
                        break;
                    }
                } else if (firstMainsiteClient === null) {
                    firstMainsiteClient = windowClient;
                }
                
                if (windowClient.url === urlToOpen) {
                    // It's a match on the mainsite
                    console.log('## WebPushServiceWorker: notificationclick event / match found', windowClient.url);
                    matchingClient = windowClient;
                    break;
                }
            }

            if (matchingClient !== null && 'focus' in matchingClient && 'navigate' in matchingClient) {
                console.log('## WebPushServiceWorker: notificationclick event / focus and navigate matching page', matchingClient.url);
                return Promise.all([
                    matchingClient.focus(),
                    (isMainsiteMatch ? matchingClient.navigate(urlToOpen) : Promise.resolve()) // Needed in case the url was javascript updated (inner navigation) as it may not match the current page anymore
                ]);
            } else if (firstMainsiteClient !== null && 'focus' in firstMainsiteClient && 'navigate' in firstMainsiteClient) {
                console.log('## WebPushServiceWorker: notificationclick event / focus and navigate first controlled page', firstMainsiteClient.url);
                return Promise.all([
                    firstMainsiteClient.focus(),
                    firstMainsiteClient.navigate(urlToOpen)
                ]);
            } else {
                console.log('## WebPushServiceWorker: notificationclick event / open new page');
                return clients.openWindow(urlToOpen);
            }
        });
    }

    const promiseChain = Promise.all([
        analyticsPromise,
        actionPromise
    ]);

    event.waitUntil(promiseChain);
});

//////////////////////////////////
// Handle notificationclose events

self.addEventListener('notificationclose', function(event) {
    console.log('## WebPushServiceWorker: notificationclose event', event);

    const dismissedNotification = event.notification;

    const analyticsPromise = self.sendAnalyticsEvent('close', dismissedNotification.data.type);

    event.waitUntil(analyticsPromise);
});

//////////////////////////////////
// Register fetch event (needed for add to home screen)
// TODO: cache mainsite pages (static content) & redirect to specific static pages (dynamyc content) to allow for offline mode navigation (full progressive web app)

self.addEventListener('fetch', function(event) {
    const urlToOpen = event.request.url;
    
    if (urlToOpen.indexOf('?utm_source=homescreen') >= 0 || urlToOpen.indexOf('?utm_source=twa') >= 0) {
        console.log('## WebPushServiceWorker v20190828-1253: handling fetch event for', urlToOpen);

        // We fetch the start_url to make sure to always serve some content when opening the progressive web app from the home screen, even offline
        event.respondWith(
            fetch( event.request, { credentials: 'include' } ).then( function(response) {
                console.log('## WebPushServiceWorker: fetched response from network is', response);

                // Online response
                return response;
            }).catch( function(error) {
                console.log('## WebPushServiceWorker: fetching failed with error', error);

                const html = '\
<body style="background-color:#4871b6;">\
    <div style="color: white; text-align:center; font-size: 2em; margin-top: 2em;">No network found! Please retry later.</div>\
    <img style="margin:auto; width:200px;display:block" src="data:image/png;base64,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"></img>\
</body>';

                // Offline response
                return new Response( html, { headers: { "Content-Type": "text/html" } } );
            })
        );
    }
});
