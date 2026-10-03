

export const SETTINGS_SCHEMAS = {

    Deye: {

        SUN_10K: {
            sections: [
                { type: "relay" },
                { type: "gridLimit" },
                { type: "essAdvanced" },
                { type: "modbusTools" }
            ]
        },

        SUN_5K: {
            sections: [
                { type: "relay" },
                { type: "essAdvanced" }
            ]
        },

        default: {
            sections: [
                { type: "relay" }
            ]
        }
    },

    Axioma: {

        AX_6K: {
            sections: [
                { type: "relay" }
            ]
        },

        default: {
            sections: [
                { type: "relay" }
            ]
        }
    },

    Victron: {

        MULTIPLUS: {
            sections: [
                { type: "essAdvanced" }
            ]
        },

        default: {
            sections: [
                { type: "essAdvanced" }
            ]
        }
    }
};
