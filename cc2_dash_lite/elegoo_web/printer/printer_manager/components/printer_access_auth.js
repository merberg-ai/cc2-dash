const PrinterAccessAuthTemplate =
    /*html*/
    `
        <div class="printer-access-auth-dialog">
            <div class="printer-access-auth-content">
                <div class="printer-image-container">
                    <img :src="connectGuideImg" />
                </div>
                <p class="instruction-text">{{ $t('printerAccessAuth.enterAccessCode') }}</p>
                
                <div class="access-code-inputs">
                    <el-input 
                        v-for="(accessCode, index) in accessCodes" 
                        :key="index"
                        :ref="el => setInputRef(el, index)"
                        type="text" 
                        class="access-code-input" 
                        maxlength="1" 
                        v-model="accessCodes[index]"
                        @input="onAccessCodeInput(index, $event)"
                        @keydown="onAccessCodeKeydown(index, $event)"
                        spellcheck="false"
                        autocorrect="off"
                    />
                </div>
                
                <a href="#" class="help-link" @click.prevent="showHelp">{{ $t('printerAccessAuth.cannotAccessCode') }}</a>

                
            </div>
            <div class="printer-access-auth-btn">
                <button class="btn-secondary" @click="closeModal">{{ $t('printerAccessAuth.close') }}</button>
                <button class="btn-primary" @click="connectPrinter">{{ $t('printerAccessAuth.connect') }}</button>
            </div>
            
            <!-- Help Dialog -->
            <el-dialog 
                v-model="showHelpDialog"
                :title="$t('printerAccessAuth.help')"
                width="400px"
                center
            >
                <p>{{ $t('printerAccessAuth.helpMessage') }}</p>
                <template #footer>
                    <span class="dialog-footer">
                        <button class="btn-primary" @click="showHelpDialog = false">{{ $t('printerAccessAuth.ok') }}</button>
                    </span>
                </template>
            </el-dialog>
        </div>
    `;

// Printer Auth Component
const PrinterAccessAuthComponent = {
    template: PrinterAccessAuthTemplate,

    props: {
        printer: {
            type: Object,
            default: null,
        },
    },

    emits: ["close-modal", "add-printer"],

    data() {
        return {
            accessCodes: ["", "", "", "", "", ""],
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
            return "./img/connect-guide-"+ defaultLanguage() +".png";
        },
    },

    watch: {
        printer: {
            handler(newVal) {
                if (newVal) {
                    this.renderPrinterAccessAuth(newVal);
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

        renderPrinterAccessAuth(printer) {
            if (!printer) return;

            // clear access code
            this.accessCodes = ["", "", "", "", "", ""];

            // set focus to first input box
            this.$nextTick(() => {
                this.focusFirstInput();
            });
        },

        getHeaderTitle() {
            const authMode = this.printer && this.printer.authMode;

            switch (authMode) {
                case 2:
                    return this.$t("printerAuth.connectToPrinter");
                case 3:
                    return this.$t("printerAuth.bindPrinter");
                default:
                    return this.$t("printerAuth.connectToPrinter");
            }
        },

        closeModal() {
            this.$emit("close-modal");
        },

        connectPrinter() {
            // merge all input values
            const accessCode = this.accessCodes.join("");

            if (accessCode.length !== 6) {
                ElementPlus.ElMessage({
                    message: this.$t("printerAccessAuth.pleaseEnterCompleteAccessCode"),
                    type: "error",
                });
                return;
            }

            const updatedPrinter = {
                ...this.printer,
                accessCode: accessCode,
            };

            this.$emit("add-printer", updatedPrinter);
        },

        onAccessCodeInput(index, value) {
            // Element Plus @input event directly passes value, not event object
            console.log('onAccessCodeInput called:', index, value);

            // ensure value is a string
            const stringValue = String(value || '');

            // only allow numbers and uppercase letters
            if (!/^[a-zA-Z0-9]*$/.test(stringValue)) {
                this.accessCodes[index] = "";
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
                            const nextInputElement = document.querySelectorAll('.access-code-input input')[index + 1];
                            if (nextInputElement) {
                                nextInputElement.focus();
                            }
                        }
                    }
                }, 10); // Small delay for Safari compatibility
            }
        },

        onAccessCodeKeydown(index, event) {
            // handle backspace key
            if (
                event.key === "Backspace" &&
                this.accessCodes[index] === "" &&
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
                            const prevInputElement = document.querySelectorAll('.access-code-input input')[index - 1];
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
                        const firstInputElement = document.querySelector('.access-code-input input');
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
