using AiPeople.UI;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AiPeople.App
{
    /// <summary>Choose the process role before any apartment/character references are deserialized.</summary>
    public sealed class PlayerBootstrap : MonoBehaviour
    {
        private void Awake()
        {
            string scene = ChatWindowApp.IsChatWindowMode() ? "ChatWindow" : "Apartment";
            Debug.Log("[AiPeople] 进程场景入口：PlayerBootstrap -> " + scene);
            SceneManager.LoadScene(scene);
        }
    }
}
