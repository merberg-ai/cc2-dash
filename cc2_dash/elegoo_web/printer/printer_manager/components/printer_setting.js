const PrinterSettingTemplate = /*html*/
    `
        <div class="printer-setting-dialog">
            <div class="printer-setting-content">
                <div class="printer-setting-left">
                    <div class="printer-setting-image-container">
                        <img class="printer-setting-image" :src="(printer && printer.printerImg) || ''" />
                    </div>
                      <el-popconfirm :title="$t('printerSetting.confirmDeletePrinter')" :confirm-button-text="$t('confirm')" 
                                     :cancel-button-text="$t('cancel')" 
                                     @confirm="deleteDevice" 
                                     width="200"
                                     cancel-button-type="info"
                                     placement="top-start">
                        <template #reference>
                            <button class="printer-setting-delete-btn" >{{ $t('printerSetting.deleteDevice') }}</button>
                        </template>
                    </el-popconfirm>
                </div>

                <div class="printer-setting-right">
                    <el-form
                        ref="printerForm"
                        :model="formData"
                        :rules="formRules"
                        label-position="top"
                        autocomplete="off"
                    >
                        <div class="printer-setting-info-item">
                            <span class="printer-setting-info-label">{{ $t('printerSetting.name') }}</span>
                            <div class="printer-setting-info-value">
                                <el-form-item prop="printerName" class="printer-setting-form-item">
                                    <el-input
                                        v-model="formData.printerName"
                                        ref="nameInput"
                                        @change="saveNameChanges"
                                        @keydown="onNameKeydown"
                                        :placeholder="$t('printerSetting.enterPrinterName')"
                                        maxlength="30"
                                    >
                                        <template #suffix>
                                           <img src="./img/edit.svg" alt="Edit"/>
                                        </template>
                                    </el-input>
                                </el-form-item>
                            </div>
                        </div>

                        <div class="printer-setting-info-item">
                            <span class="printer-setting-info-label">{{ $t('printerSetting.model') }}</span>
                            <span class="printer-setting-info-value">{{ (printer && printer.printerModel) || '' }}</span>
                        </div>

                        <div class="printer-setting-info-item" v-if="printer && printer.networkType === 0">
                            <span class="printer-setting-info-label">{{ $t('printerSetting.host') }}</span>
                            <div class="printer-setting-info-value">
                                <el-form-item  prop="host" class="printer-setting-form-item">
                                    <el-input
                                        v-model="formData.host"
                                        ref="hostInput"
                                        @change="saveHostChanges"
                                        @keydown="onHostKeydown"
                                        :placeholder="$t('printerSetting.enterHostIpUrl')"
                                        maxlength="30"
                                    >
                                        <template #suffix>
                                            <img src="./img/edit.svg" alt="Edit"/>
                                        </template>
                                    </el-input>
                                </el-form-item>
                            </div>
                        </div>

                        <div class="printer-setting-info-item" v-if="printer && printer.networkType === 1">
                            <span class="printer-setting-info-label">{{ $t('printerSetting.serialNumber') }}</span>
                            <span class="printer-setting-info-value">
                                <span class="printer-setting-info-text">
                                    <span class="printer-setting-firmware-version">{{ (printer && printer.serialNumber) || '' }}</span>
                                </span>
                            </span>
                        </div>

                        <div class="printer-setting-info-item">
                            <span class="printer-setting-info-label">{{ $t('printerSetting.firmware') }}</span>
                            <span class="printer-setting-info-value">
                                <span class="printer-setting-info-text">
                                    <span class="printer-setting-firmware-version">{{ (printer && printer.firmwareVersion) || '' }}</span>
                                    <span class="printer-setting-update-tag" :class="{ available: printer && printer.firmwareUpdate === 1 }" v-if="false">
                                        {{ getFirmwareUpdateText() }}
                                    </span>
                                </span>
                                <button v-if="showRefreshButton" class="printer-setting-refresh-btn" @click="checkFirmwareUpdate">
                                    <img src="img/refresh.svg" alt="Refresh" />
                                </button>
                            </span>
                        </div>
                    </el-form>
                </div>
            </div>
        </div>
    `;


// Printer Setting Component
const PrinterSettingComponent = {
    template: PrinterSettingTemplate,
    setup() {
        const printerStore = usePrinterStore();
        return {
            printerStore
        };
    },
    props: {
        printer: {
            type: Object,
            default: null
        }
    },

    emits: ['close-modal'],

    data() {
        return {
            formData: {
                printerName: '',
                host: ''
            },
            formRules: {
                printerName: [
                    { required: true, message: this.$t('printerSetting.pleaseEnterPrinterName'), trigger: 'change' },
                    {
                        validator: (rule, value, callback) => {
                            if (!value || !value.trim()) {
                                callback(new Error(this.$t('printerSetting.pleaseEnterPrinterName')));
                                return;
                            }
                            const trimmedValue = value.trim();
                            // Check length (1-30 characters)
                            if (trimmedValue.length < 1 || trimmedValue.length > 30) {
                                callback(new Error(this.$t('printerSetting.lengthShouldBe1To30')));
                                return;
                            }
                            // Support all languages and specified special characters (including spaces)
                            const nameRegex = /^[\p{L}\p{N} ・!@#$‰^&*()_+=\-`~\[\]{};:'",.\\s<>/?.|]+$/u;
                            if (!nameRegex.test(trimmedValue)) {
                                callback(new Error(this.$t('printerSetting.invalidPrinterName')));
                                return;
                            }
                            callback();
                        },
                        trigger: 'change'
                    }
                ],
                host: [
                    { required: true, message: this.$t('printerSetting.pleaseEnterHostNameIpUrl'), trigger: 'blur' },
                    {
                        validator: (rule, value, callback) => {
                            this.printerStore.validateHost(rule, value, callback);
                        },
                        trigger: 'blur'
                    }
                ]
            }
        };
    },

    computed: {
        showRefreshButton() {
            return this.printer && this.printer.firmwareUpdate === 1;
        }
    },

    mounted() {
        // Initialize form data with current printer data
        this.syncFormData();
    },

    beforeUnmount() {
        // Reset editing state when component is unmounted
        this.resetEditingState();
    },

    watch: {
        // Watch for printer prop changes to sync form data
        printer: {
            handler(newPrinter) {
                if (newPrinter) {
                    this.syncFormData();
                    this.resetEditingState();
                }
            },
            immediate: true
        }
    },

    methods: {
        closeModal() {
            // Reset editing state before closing modal
            this.resetEditingState();
            this.$emit('close-modal');
        },

        async deleteDevice() {
            await this.printerStore.requestDeletePrinter(this.printer.printerId);
            this.closeModal();

        },

        onNameKeydown(e) {
            if (e.keyCode === 13) { // Enter
                e.preventDefault();
                // this.saveNameChanges();
                this.$refs.nameInput.blur(); // 失去焦点
            } else if (e.keyCode === 27) { // Escape
                e.preventDefault();
                this.cancelNameChanges();
            }
        },

        onHostKeydown(e) {
            if (e.keyCode === 13) { // Enter
                e.preventDefault();
                // this.saveHostChanges();
                this.$refs.hostInput.blur(); // 失去焦点
            } else if (e.keyCode === 27) { // Escape
                e.preventDefault();
                this.cancelHostChanges();
            }
        },

        saveNameChanges() {
            const newName = this.formData.printerName.trim();
            const currentName = (this.printer && this.printer.printerName) || '';

            if (newName && newName !== currentName) {
                // Use Element Plus form validation
                this.$refs.printerForm.validateField('printerName', async (valid) => {
                    if (valid) {
                        try {
                            await this.printerStore.requestUpdatePrinterName(this.printer.printerId, newName);
                            this.printer.printerName = newName;
                        } catch (error) {
                            this.formData.printerName = currentName;
                        }
                    } else {
                        // Validation failed, keep editing mode and show error
                        console.log('Name validation failed');
                    }
                });
            }
        },

        saveHostChanges() {
            const newHost = this.formData.host.trim();
            const currentHost = (this.printer && this.printer.host) || '';

            if (newHost && newHost !== currentHost) {
                // Use Element Plus form validation
                this.$refs.printerForm.validateField('host', async (valid) => {
                    if (valid) {
                        try {
                            await this.printerStore.requestUpdatePrinterHost(this.printer.printerId, newHost);
                            this.printer.host = newHost;
                        } catch (error) {
                            this.formData.host = currentHost;
                        }
                    } else {
                        // Validation failed, keep editing mode and show error
                        console.log('Host validation failed');
                    }
                });
            }
        },

        cancelNameChanges() {
            // Restore original value
            this.formData.printerName = (this.printer && this.printer.printerName) || '';
            // Clear validation errors
            if (this.$refs.printerForm) {
                this.$refs.printerForm.clearValidate('printerName');
            }
        },

        cancelHostChanges() {
            // Restore original value
            this.formData.host = (this.printer && this.printer.host) || '';
            // Clear validation errors
            if (this.$refs.printerForm) {
                this.$refs.printerForm.clearValidate('host');
            }
        },

        checkFirmwareUpdate() {
            // this.$emit('check-firmware-update');
        },

        getFirmwareUpdateText() {
            if (this.printer && this.printer.firmwareUpdate === 1) {
                return this.$t('printerSetting.updateAvailable');
            }
            return this.$t('printerSetting.latestVersion');
        },

        // Sync form data with current printer data
        syncFormData() {
            if (this.printer) {
                this.formData.printerName = this.printer.printerName || '';
                this.formData.host = this.printer.host || '';
            }
        },

        // Reset editing state to display mode
        resetEditingState() {
            // Clear any validation errors
            if (this.$refs.printerForm) {
                this.$refs.printerForm.clearValidate();
            }
        }
    }
};
