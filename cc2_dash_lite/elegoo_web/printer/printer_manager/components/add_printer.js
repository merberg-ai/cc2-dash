
const AddPrinterTemplate = /*html*/`
        <div class="add-printer-dialog">
          
            <el-tabs v-model="activeTab" class="add-printer-tabs" @tab-change="switchTab">
                <el-tab-pane :label="$t('addPrinterDialog.discover')" class="add-printer-tabpane" name="discover">
                    <div class="add-printer-table">
                        <div class="add-printer-table-header">
                                <span>{{ $t('addPrinterDialog.device') }}</span>
                                <span>{{ $t('addPrinterDialog.info') }}</span>
                                <span>{{ $t('addPrinterDialog.status') }}</span>
                        </div>
                        <div v-show="isLoading" >
                            <div class="linear-loading"></div>
                        </div>
                        <div v-if="printers && printers.length === 0" class="discover-empty">
                            <img src="./img/discover-empty.svg" width="180" />
                            <div>{{ $t('addPrinterDialog.noDevicesFound') }}</div>
                        </div>
                        <div v-else class="add-printer-table-body">
                            <div v-for="(printer, idx) in printers" 
                                    :key="idx"
                                    :class="printerLineClass(idx)"
                                    @click="selectPrinter(idx)"
                                    @dblclick="connectPrinter">
                            
                                <span>
                                    <img :src="printer.printerImg" class="add-printer-img"/>
                                </span>
                                <span>
                                    <span class="add-printer-name">{{ printer.printerName }}</span><br>
                                    <span class="add-printer-host">{{ printer.host }}</span>
                                </span>
                                <span>
                                    <span class="add-printer-status">{{ getPrinterStatusText(printer) }}</span>
                                </span>
                            </div>
                        </div>

                    </div>
                </el-tab-pane>
                <el-tab-pane :label="$t('addPrinterDialog.manualAdd')" class="add-printer-tabpane"  name="manual">
                        <div class="add-printer-content">
                                <manual-form-component 
                                    ref="manualFormComponent"
                                    :printer="null" ></manual-form-component>
                        </div>
                </el-tab-pane>
            </el-tabs>

            <el-dialog v-model="showPrinterAccessAuth" :title="$t('addPrinterDialog.connectToPrinter')" width="600" center>
                <printer-access-auth-component ref="printerAccessAuthComponent" :printer="pendingAccessAuthPrinter" @close-modal="clearAccessAuthData"
                    @add-printer="connectPrinterAuth"></printer-access-auth-component>
            </el-dialog>

            <el-dialog v-model="showPrinterPinAuth" :title="$t('addPrinterDialog.bindPrinter')" width="600" center>
                <printer-pin-auth-component ref="printerPinAuthComponent" :printer="pendingPinAuthPrinter" @close-modal="clearPinAuthData"
                    @add-printer="connectPrinterAuth"></printer-pin-auth-component>
            </el-dialog>

            <el-dialog v-model="showPinAuthLoading" :title="$t('addPrinterDialog.bindPrinter')" width="600" center @close="handleCancelPinAuth">
                <pin-auth-loading-component></pin-auth-loading-component>
            </el-dialog>

            <button type="button" :disabled="isLoading" v-show="activeTab==='discover'" class="btn-primary" style=" position: absolute; right: 0px; top: 0px;" @click="requestDiscoverPrinters">{{ $t('addPrinterDialog.refresh') }}</button>
            <div class="manual-add-note" v-show="activeTab==='manual'">{{ $t('manualForm.manualAddNote') }}</div>
            <div class="add-printer-footer">
                <button type="button" class="btn-secondary" @click="closeModal">{{ $t('addPrinterDialog.close') }}</button>   
                <span class="help-link" v-show="activeTab==='discover'" @click.prevent="showHelp" style="align-self: flex-end;">{{ $t('addPrinterDialog.cannotFindPrinter') }}</span>
                <button type="button" class="btn-primary" @click="connectPrinter">{{ $t('addPrinterDialog.connect') }}</button>
            </div>

                        <!-- Help Dialog -->
            <el-dialog 
                v-model="showHelpDialog"
                :title="$t('addPrinterDialog.help')"
                width="400px"
                center
            >
                <p>{{ $t('addPrinterDialog.helpMessage') }}</p>
                <template #footer>
                    <span class="dialog-footer">
                        <button class="btn-primary" @click="showHelpDialog = false">{{ $t('addPrinterDialog.ok') }}</button>
                    </span>
                </template>
            </el-dialog>
        </div>
    `;


// Add Printer Component
const AddPrinterComponent = {
    template: AddPrinterTemplate,

    setup() {
        const printerStore = usePrinterStore();
        return {
            printerStore
        };
    },
    props: {

    },
    emits: ['close-modal'],
    data() {
        return {
            activeTab: 'discover',
            selectedPrinterIdx: null,
            showPrinterAccessAuth: false,
            showPrinterPinAuth: false,
            pendingAccessAuthPrinter: null,
            pendingPinAuthPrinter: null,
            showHelpDialog: false,
            showPinAuthLoading: false,
            userCancelledPinAuth: false,
            currentConnectingPrinter: null
        };
    },

    mounted() {
    },

    watch: {
    },

    beforeUnmount() {
    },

    computed: {
        printers() {
            return this.printerStore.discoveredPrinters;
        },
        isLoading() {
            return this.printerStore.isDiscovering;
        }
    },

    methods: {
        switchTab(tab) {
            this.activeTab = tab;
        },

        clearAccessAuthData() {
            this.pendingAccessAuthPrinter = null;
            this.showPrinterAccessAuth = false;
            this.$nextTick(() => {
                if (this.$refs.printerAccessAuthComponent) {
                    this.$refs.printerAccessAuthComponent.accessCodes = ["", "", "", "", "", ""];
                }
            });
        },

        clearPinAuthData() {
            this.pendingPinAuthPrinter = null;
            this.showPrinterPinAuth = false;
            this.showPinAuthLoading = false;
            this.currentConnectingPrinter = null;
            this.$nextTick(() => {
                if (this.$refs.printerPinAuthComponent) {
                    this.$refs.printerPinAuthComponent.pinCodes = ["", "", "", "", "", ""];
                }
            });
        },

        closeModal() {
            if (this.$refs.manualFormComponent) {
                this.$refs.manualFormComponent.resetForm();
            }
            this.$emit('close-modal');
        },

        async connectPrinter() {
            try {
                if (this.activeTab === 'discover') {
                    if (this.selectedPrinterIdx === null) {
                        ElementPlus.ElMessage({
                            message: this.$t('addPrinterDialog.pleaseSelectPrinter'),
                            type: 'error',
                        });
                        return;
                    }
                    const printer = this.printers[this.selectedPrinterIdx];

                    // Check if printer is already added
                    if (printer.isAdded) {
                        const statusKey = printer.networkType === 1 ? 'addPrinterDialog.alreadyBinded' : 'addPrinterDialog.alreadyConnected';
                        ElementPlus.ElMessage({
                            message: this.$t(statusKey),
                            type: 'error',
                        });
                        return;
                    }

                    // authMode === 2: requires access code
                    if (printer.authMode === 2 && (printer.accessCode === '' || printer.accessCode === null)) {
                        this.pendingAccessAuthPrinter = printer;
                        this.showPrinterAccessAuth = true;
                        this.$nextTick(() => {
                            if (this.$refs.printerAccessAuthComponent) {
                                this.$refs.printerAccessAuthComponent.accessCodes = ["", "", "", "", "", ""];
                                this.$refs.printerAccessAuthComponent.focusFirstInput();
                            }
                        });
                        return;
                    }

                    // authMode === 3: requires pin code
                    if (printer.authMode === 3 && (printer.pinCode === '' || printer.pinCode === null)) {
                        this.pendingPinAuthPrinter = printer;
                        this.showPrinterPinAuth = true;
                        this.$nextTick(() => {
                            if (this.$refs.printerPinAuthComponent) {
                                this.$refs.printerPinAuthComponent.pinCodes = ["", "", "", "", "", ""];
                                this.$refs.printerPinAuthComponent.focusFirstInput();
                            }
                        });
                        return;
                    }

                    // no auth required or auth already provided
                    await this.printerStore.requestAddPrinter(printer);
                    return;
                } else {
                    // manual add tab
                    if (this.$refs.manualFormComponent) {
                        const printer = await this.$refs.manualFormComponent.getFormData();
                        if (printer) {
                            // Check if this is cloud connect (networkType === 1)
                            const isCloudConnect = printer.networkType === 1;
                            
                            if (isCloudConnect) {
                                // Show loading dialog for cloud connect
                                this.showPinAuthLoading = true;
                                this.userCancelledPinAuth = false;
                                this.currentConnectingPrinter = printer;
                            }
                            
                            try {
                                await this.printerStore.requestAddPhysicalPrinter(printer, !isCloudConnect, isCloudConnect ? 4 * 60 * 1000 :  60 * 1000);
                                
                                // Success: close all dialogs and reset form
                                this.$refs.manualFormComponent.resetForm();
                                this.clearPinAuthData();
                                this.closeModal();
                                
                            } catch (error) {
                                if (isCloudConnect) {
                                    // Hide loading dialog
                                    this.showPinAuthLoading = false;
                                    
                                    // Only clear if user didn't cancel
                                    if (this.userCancelledPinAuth) {
                                        this.clearPinAuthData();
                                    }
                                }
                                throw error;
                            }
                        }
                    }
                }

            } catch (error) {
                console.error('Failed to connect printer:', error);
            }
        },

        async connectPrinterAuth(printer) {
            try {
                // Check if printer is already added
                if (printer.isAdded) {
                    const statusKey = printer.networkType === 1 ? 'addPrinterDialog.alreadyBinded' : 'addPrinterDialog.alreadyConnected';
                    ElementPlus.ElMessage({
                        message: this.$t(statusKey),
                        type: 'error',
                    });
                    return;
                }

                // Check if this is PIN auth (networkType === 1)
                const isCloudConnect = printer.networkType === 1;

                if (isCloudConnect) {
                    // Show loading dialog and hide auth dialog for PIN auth
                    this.showPrinterPinAuth = false;
                    this.showPinAuthLoading = true;
                    this.userCancelledPinAuth = false;
                    this.currentConnectingPrinter = printer;
                }

                // Attempt to connect
                await this.printerStore.requestAddPrinter(printer, !isCloudConnect, isCloudConnect ? 4 * 60 * 1000 :  60 * 1000);

                // Success: close all dialogs and clear auth info
                this.clearAccessAuthData();
                this.clearPinAuthData();

            } catch (error) {
                console.error('Failed to connect printer:', error);

                // Check if this is Cloud Connect
                const isCloudConnect = printer.networkType === 1;

                if (isCloudConnect) {
                    // Hide loading dialog
                    this.showPinAuthLoading = false;

                    // Only re-show auth dialog if user didn't cancel
                    if (!this.userCancelledPinAuth) {
                        this.showPrinterPinAuth = true;
                        this.$nextTick(() => {
                            if (this.$refs.printerPinAuthComponent) {
                                this.$refs.printerPinAuthComponent.focusFirstInput();
                            }
                        });
                    } else {
                        // User cancelled, clear everything
                        this.clearPinAuthData();
                    }
                } else {
                    // For access code auth, just clear
                    this.clearAccessAuthData();
                }
            }
        },

        requestDiscoverPrinters() {
            this.printerStore.requestDiscoverPrinters();
        },

        selectPrinter(idx) {
            this.selectedPrinterIdx = idx;
        },

        printerLineClass(idx) {
            return {
                'add-printer-table-tr': true,
                'selected': this.selectedPrinterIdx === idx
            };
        },
        getPrinterStatusText(printer) {
            if (printer.isAdded) {
                if (printer.networkType === 1) {
                    return this.$t('addPrinterDialog.binded');
                } else {
                    return this.$t('addPrinterDialog.connected');
                }
            } else {
                if (printer.networkType === 1) {
                    return this.$t('addPrinterDialog.bindable');
                } else {
                    return this.$t('addPrinterDialog.connectable');
                }
            }
              
        },
        showHelp() {
            this.showHelpDialog = true;
        },

        async handleCancelPinAuth() {
            if(!this.showPinAuthLoading){
                return;
            }

            // User closed the loading dialog - mark as cancelled
            this.userCancelledPinAuth = true;
            try {
                // Send cancel request to backend
                if (this.currentConnectingPrinter) {
                    await this.printerStore.requestCancelAddPrinter(this.currentConnectingPrinter);
                }
            } catch (error) {
                console.error('Failed to cancel add printer:', error);
            }


            this.showPinAuthLoading = false;
            // Clear all auth-related data
            this.clearPinAuthData();
        }
    }
};
