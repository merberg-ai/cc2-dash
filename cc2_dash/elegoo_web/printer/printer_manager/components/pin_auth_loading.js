const PinAuthLoadingTemplate =
    /*html*/
    `
        <div class="printer-pin-auth-dialog">
            <div class="printer-pin-auth-content">
                <div class="printer-image-container">
                    <img :src="connectGuideImg" />
                </div>
                <p class="instruction-text">{{ $t('addPrinterDialog.bindingInProgress') }}</p> 
                <p class="tip-text">{{ $t('addPrinterDialog.bindingInProgressTip') }}</p>       
            </div>       
        </div>
    `;

// Pin Auth Loading Component
const PinAuthLoadingComponent = {
    template: PinAuthLoadingTemplate,
    computed: {
        connectGuideImg() {
            return "./img/pincode-connect-guide-" + defaultLanguage() + ".png";
        },
    },
};
