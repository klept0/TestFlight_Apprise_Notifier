import React, { useState, useEffect } from "react";
import axios from "axios";

const Dashboard = () => {
    const [ids, setIds] = useState([]);
    const [newId, setNewId] = useState("");
    const socket = new WebSocket("ws://localhost:8089/ws");

    useEffect(() => {
        // Fetch initial data
        axios.get("/api/testflight-ids").then((response) => {
            setIds(response.data.testflight_ids);
        });

        // WebSocket live updates
        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === "update") {
                setIds(data.testflight_ids);
            }
        };

        return () => socket.close();
    }, []);

    const addId = async () => {
        if (newId.trim() === "") return;
        try {
            await axios.post(`/api/add-id/${newId}`);
            setNewId("");
        } catch (error) {
            alert(error.response?.data?.detail || "Error adding ID");
        }
    };

    const removeId = async (id) => {
        try {
            await axios.delete(`/api/remove-id/${id}`);
        } catch (error) {
            alert(error.response?.data?.detail || "Error removing ID");
        }
    };

    return (
        <div className="p-5">
            <h1 className="text-2xl font-bold mb-4">TestFlight ID Manager</h1>
            <div className="flex gap-2 mb-4">
                <input
                    type="text"
                    className="border p-2 w-64"
                    placeholder="Enter new TestFlight ID"
                    value={newId}
                    onChange={(e) => setNewId(e.target.value)}
                />
                <button onClick={addId} className="bg-blue-500 text-white p-2">
                    Add ID
                </button>
            </div>
            <ul>
                {ids.map((id) => (
                    <li key={id} className="flex justify-between border p-2 mb-2">
                        <span>{id}</span>
                        <button onClick={() => removeId(id)} className="bg-red-500 text-white p-1">
                            Remove
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    );
};

export default Dashboard;
