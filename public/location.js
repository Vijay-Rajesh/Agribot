(() => {
  const buttonId = "farmbot-share-location";

  function addLocationButton() {
    const composer = document.querySelector("textarea");
    const panel = composer && composer.parentElement?.parentElement;
    if (!panel || panel.querySelector(`#${buttonId}`)) return;

    const button = document.createElement("button");
    button.id = buttonId;
    button.type = "button";
    button.textContent = "Share live location";
    button.title = "Share your current location with FarmBot";
    button.style.cssText =
      "border:1px solid #548b54;border-radius:8px;padding:6px 10px;" +
      "margin:4px;color:inherit;background:transparent;cursor:pointer;";

    button.addEventListener("click", () => {
      if (!navigator.geolocation) {
        window.alert("This browser does not support location sharing.");
        return;
      }

      button.disabled = true;
      button.textContent = "Getting location...";
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const latitude = position.coords.latitude.toFixed(6);
          const longitude = position.coords.longitude.toFixed(6);
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype,
            "value"
          ).set;
          setter.call(composer, `My live location: ${latitude}, ${longitude}`);
          composer.dispatchEvent(new Event("input", { bubbles: true }));
          button.textContent = "Location shared";
          window.setTimeout(() => {
            panel.querySelector("#chat-submit")?.click();
          }, 50);
          window.setTimeout(() => {
            button.disabled = false;
            button.textContent = "Share live location";
          }, 1500);
        },
        (error) => {
          button.disabled = false;
          button.textContent = "Share live location";
          const message =
            error.code === error.PERMISSION_DENIED
              ? "Location permission was denied. Allow location access in your browser settings and try again."
              : "Couldn't get your location. Check your device's location settings and try again.";
          window.alert(message);
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
      );
    });

    const actionRow = panel.children[1];
    const target = actionRow?.children[1] || panel;
    target.prepend(button);
  }

  new MutationObserver(addLocationButton).observe(document.body, {
    childList: true,
    subtree: true,
  });
  addLocationButton();
})();
