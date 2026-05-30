const PrinterPinAuthTemplate =
    /*html*/
    `
        <div class="printer-pin-auth-dialog">
            <div class="printer-pin-auth-content">
                <div class="printer-image-container">
                    <img :src="connectGuideImg" />
                </div>
                <p class="instruction-text">{{ $t('printerPinAuth.enterPinCode') }}</p>
                
                <div class="pin-code-inputs">
                    <el-input 
                        v-for="(pinCode, index) in pinCodes" 
                        :key="index"
                        :ref="el => setInputRef(el, index)"
                        type="text" 
                        class="pin-code-input" 
                        maxlength="1" 
                        v-model="pinCodes[index]"
                        @input="onPinCodeInput(index, $event)"
                        @keydown="onPinCodeKeydown(index, $event)"
                        spellcheck="false"
                        autocorrect="off"
                    />
                </div>
                
                <a href="#" class="help-link" @click.prevent="showHelp">{{ $t('printerPinAuth.cannotPinCode') }}</a>

                
            </div>
            <div class="printer-pin-auth-btn">
                <button class="btn-secondary" @click="closeModal">{{ $t('printerPinAuth.close') }}</button>
                <button class="btn-primary" @click="connectPrinter">{{ $t('printerPinAuth.connect') }}</button>
            </div>
            
            <!-- Help Dialog -->
            <el-dialog 
                v-model="showHelpDialog"
                :title="$t('printerPinAuth.help')"
                width="400px"
                center
            >
                <p>{{ $t('printerPinAuth.helpMessage') }}</p>
                <template #footer>
                    <span class="dialog-footer">
                        <button class="btn-primary" @click="showHelpDialog = false">{{ $t('printerPinAuth.ok') }}</button>
                    </span>
                </template>
            </el-dialog>
        </div>
    `;

// Printer Auth Component
const PrinterPinAuthComponent = {
    template: PrinterPinAuthTemplate,

    props: {
        printer: {
            type: Object,
            default: null,
        },
    },

    emits: ["close-modal", "add-printer"],

    data() {
        return {
            pinCodes: ["", "", "", "", "", ""],
            inputRefs: [],
            showHelpDialog: false,
        };
    },

    mounted() {
        this.$nextTick(() => {
            this.focusFirstInput();
        });
    },

    beforeUnmount() {

    },

    computed: {
        connectGuideImg() {
            return "./img/pincode-connect-guide-" + defaultLanguage() + ".png";
        },
    },

    watch: {
        printer: {
            handler(newVal) {
                if (newVal) {
                    this.renderPrinterPinAuth(newVal);
                }
            },
            immediate: true,
        },
    },

    methods: {
        // set input box reference
        setInputRef(el, index) {
            if (el) {
                this.inputRefs[index] = el;
            }
        },

        renderPrinterPinAuth(printer) {
            if (!printer) return;

            // clear pin code
            this.pinCodes = ["", "", "", "", "", ""];

            // set focus to first input box
            this.$nextTick(() => {
                this.focusFirstInput();
            });
        },

        getHeaderTitle() {
            return this.$t("printerAuth.bindPrinter");
        },

        closeModal() {
            this.$emit("close-modal");
        },

        connectPrinter() {
            // merge all input values
            const pinCode = this.pinCodes.join("");

            if (pinCode.length !== 6) {
                ElementPlus.ElMessage({
                    message: this.$t("printerPinAuth.pleaseEnterCompletePinCode"),
                    type: "error",
                });
                return;
            }

            const updatedPrinter = {
                ...this.printer,
                pinCode: pinCode,
            };

            this.$emit("add-printer", updatedPrinter);
        },

        onPinCodeInput(index, value) {
            // Element Plus @input event directly passes value, not event object
            console.log('onPinCodeInput called:', index, value);

            // ensure value is a string
            const stringValue = String(value || '');

            // only allow numbers and uppercase letters
            if (!/^[a-zA-Z0-9]*$/.test(stringValue)) {
                this.pinCodes[index] = "";
                return;
            }

            // if a character is entered, automatically focus on the next input box
            if (stringValue.length === 1 && index < 5) {
                // Use setTimeout for better compatibility with Safari
                setTimeout(() => {
                    const nextInput = this.inputRefs[index + 1];
                    if (nextInput) {
                        // Try multiple methods to ensure focus works in Safari
                        try {
                            // Method 1: Direct focus on Element Plus component
                            if (nextInput.focus) {
                                nextInput.focus();
                            }
                            // Method 2: Focus on the inner input element
                            else if (nextInput.$refs && nextInput.$refs.input) {
                                nextInput.$refs.input.focus();
                            }
                            // Method 3: Find and focus the actual DOM input element (Safari fallback)
                            else if (nextInput.$el) {
                                const inputElement = nextInput.$el.querySelector('input');
                                if (inputElement) {
                                    inputElement.focus();
                                }
                            }
                        } catch (error) {
                            console.warn('Focus failed:', error);
                            // Fallback: try to find input by DOM traversal
                            const nextInputElement = document.querySelectorAll('.pin-code-input input')[index + 1];
                            if (nextInputElement) {
                                nextInputElement.focus();
                            }
                        }
                    }
                }, 10); // Small delay for Safari compatibility
            }
        },

        onPinCodeKeydown(index, event) {
            // handle backspace key
            if (
                event.key === "Backspace" &&
                this.pinCodes[index] === "" &&
                index > 0
            ) {
                // Use setTimeout for better compatibility with Safari
                setTimeout(() => {
                    const prevInput = this.inputRefs[index - 1];
                    if (prevInput) {
                        // Try multiple methods to ensure focus works in Safari
                        try {
                            // Method 1: Direct focus on Element Plus component
                            if (prevInput.focus) {
                                prevInput.focus();
                            }
                            // Method 2: Focus on the inner input element
                            else if (prevInput.$refs && prevInput.$refs.input) {
                                prevInput.$refs.input.focus();
                            }
                            // Method 3: Find and focus the actual DOM input element (Safari fallback)
                            else if (prevInput.$el) {
                                const inputElement = prevInput.$el.querySelector('input');
                                if (inputElement) {
                                    inputElement.focus();
                                }
                            }
                        } catch (error) {
                            console.warn('Focus failed:', error);
                            // Fallback: try to find input by DOM traversal
                            const prevInputElement = document.querySelectorAll('.pin-code-input input')[index - 1];
                            if (prevInputElement) {
                                prevInputElement.focus();
                            }
                        }
                    }
                }, 10); // Small delay for Safari compatibility
            }
        },

        focusFirstInput() {
            // Use setTimeout for better compatibility with Safari
            setTimeout(() => {
                const firstInput = this.inputRefs[0];
                if (firstInput) {
                    // Try multiple methods to ensure focus works in Safari
                    try {
                        // Method 1: Direct focus on Element Plus component
                        if (firstInput.focus) {
                            firstInput.focus();
                        }
                        // Method 2: Focus on the inner input element
                        else if (firstInput.$refs && firstInput.$refs.input) {
                            firstInput.$refs.input.focus();
                        }
                        // Method 3: Find and focus the actual DOM input element (Safari fallback)
                        else if (firstInput.$el) {
                            const inputElement = firstInput.$el.querySelector('input');
                            if (inputElement) {
                                inputElement.focus();
                            }
                        }
                    } catch (error) {
                        console.warn('Focus failed:', error);
                        // Fallback: try to find first input by DOM traversal
                        const firstInputElement = document.querySelector('.pin-code-input input');
                        if (firstInputElement) {
                            firstInputElement.focus();
                        }
                    }
                }
            }, 50); // Slightly longer delay for initial focus
        },

        showHelp() {
            this.showHelpDialog = true;
        },
    },
};
